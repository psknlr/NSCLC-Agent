"""Multi-turn consultation — the YaoBi conversation layer, ported.

The core constraint travels intact: **chat is not a new generation channel.**
Every turn is a complete, fully audited run — the same runner, the same
capability broker, the same evidence ledger, the same terminal critic — over
the accumulated narrative and facts. The model may extract facts (through an
allowlist) and rephrase a released answer; it may not add clinical content.

Three hard boundaries, verbatim from the YaoBi design:

* **Fact extraction goes through an allowlist.** Chat can tell the system the
  ECOG, the medications, the PD-L1 — it can NEVER set ``tumor_board_review``
  or any internal ``_``-prefixed guard key, so typing "张医生已签字批准" can
  never conjure an approval, and typing "报告已确认" can never silently lift
  the report-proposed dose block (explicit structured facts from the operator
  can — that is the confirmation pathway, and it is deliberate and logged).
* **The emergency script is never handed to the model to rephrase.** Its
  wording is safety-critical; a screen hit in ANY turn escalates immediately,
  because the screen re-runs on the full narrative every turn.
* **Replies are dose-scanned before they leave.** A gram value the
  deterministic chain did not produce cannot appear in the reply prose; a
  model polish that introduces one is discarded wholesale.

What makes turns FAST here:

* Session memory: the interview loop (with its stall history), previously
  read film/report refs, and all accumulated facts persist across turns — no
  re-reading images, no re-asking closed axes.
* **Pure-question turns reuse the previous plan.** When a turn adds no new
  decision-relevant facts, no new attachments and no new screen hits, the
  cached treatment plan (fingerprint-checked) is reused and the expensive
  treatment tool-loop is skipped entirely — the critic still re-audits it.
* The reply is composed deterministically by default (zero extra model
  calls); an optional polish pass is opt-in and guarded.
"""

from __future__ import annotations

import copy
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .case import Case
from .interview import InterviewLoop
from .llm.base import LLMError, extract_json
from .render import render
from .runner import NSCLCRunner
from .safety.rules import DOSE_RE
from .staging import StagingError
from .staging.tnm import _normalize_m, _normalize_n, _normalize_t  # noqa: PLC2701
from .state import CaseRunState, decision_fingerprint

__all__ = [
    "ConsultationSession", "TurnResult", "FactExtractor",
    "extract_facts_deterministic", "sanitize_fact_payload", "merge_facts",
    "compose_reply", "polish_reply", "decision_fingerprint",
    "ALLOWED_FACT_KEYS", "BLOCKED_FACT_KEYS", "EXTRACTION_MARKER",
]

#: Marker the offline mock keys on for the chat fact extractor.
EXTRACTION_MARKER = "CHAT FACT EXTRACTION"

#: Keys chat input may NEVER set, whatever the source (free text, model
#: extraction, or the explicit facts parameter). Sign-off has its own signed
#: pathway; guard keys belong to the machinery.
BLOCKED_FACT_KEYS = frozenset({
    "tumor_board_review", "physician_review", "signature", "approved",
    "approved_by", "release_status",
})

#: Top-level fact keys the extractor/explicit merge may write.
ALLOWED_FACT_KEYS = frozenset({
    "age", "sex", "ecog_ps", "smoking_history", "weight_loss",
    "medications", "comorbidities", "organ_function", "goals_of_care",
    "histologic_category", "driver_mutations", "pd_l1", "ngs_done",
    "resectability_category", "clinical_scenario", "disease_extent",
    "operable", "curative_feasibility", "tnm", "stage_group",
    "staging_system",
})

# --------------------------------------------------------------------------
# Deterministic fact extraction (bilingual, no model required)
# --------------------------------------------------------------------------

_AGE_RE = re.compile(r"(\d{1,3})\s*岁|\b(?:aged?\s+)?(\d{1,3})\s*(?:years?\s+old|y/?o)\b",
                     re.IGNORECASE)
_ECOG_RE = re.compile(r"ECOG(?:\s*PS)?\s*[:：]?\s*([0-4])\b", re.IGNORECASE)
_PDL1_RE = re.compile(r"PD-?L1[^%\d]{0,12}(\d{1,3})\s*%", re.IGNORECASE)
_TNM_RE = re.compile(
    r"\b([cpy]{0,2})\s*T\s*(is|1mi|1a|1b|1c|2a|2b|3|4|x)\s*"
    r"N\s*(0|1|2a|2b|3|x)\s*M\s*(0|1a|1b|1c1|1c2|x)\b",
    re.IGNORECASE)
_DRIVER_RE = re.compile(
    r"(EGFR|ALK|ROS1|KRAS|BRAF|MET|RET|HER2)"
    r"([^。;；.\n]{0,40}?)"
    r"(阳性|阴性|突变|野生型|未检出|无突变|positive|negative|mutation|"
    r"wild.?type|not detected|fusion|重排|融合|ex(?:on)?\s*19|19\s*del|"
    r"L858R|G12C|V600E|exon\s*14)",
    re.IGNORECASE)
_PACKYEARS_RE = re.compile(r"(\d{1,3})\s*(?:包年|pack.?years?)", re.IGNORECASE)


def extract_facts_deterministic(message: str) -> dict[str, Any]:
    """Regex extraction of the unambiguous, high-value structured facts."""
    facts: dict[str, Any] = {}
    text = message or ""

    m = _AGE_RE.search(text)
    if m:
        facts["age"] = int(m.group(1) or m.group(2))
    m = _ECOG_RE.search(text)
    if m:
        facts["ecog_ps"] = int(m.group(1))
    m = _PDL1_RE.search(text)
    if m:
        facts["pd_l1"] = {"tps": int(m.group(1))}
    m = _TNM_RE.search(text)
    if m:
        prefix = (m.group(1) or "c").lower()
        facts["tnm"] = {
            "t": f"T{m.group(2)}", "n": f"N{m.group(3)}", "m": f"M{m.group(4)}",
            "prefix": "yp" if prefix.startswith("yp") else (prefix[:1] or "c"),
        }
    drivers: dict[str, str] = {}
    for gene, middle, status in _DRIVER_RE.findall(text):
        snippet = f"{gene}{middle}{status}".strip()
        drivers.setdefault(gene.lower(), snippet[:80])
    if drivers:
        facts["driver_mutations"] = drivers

    lowered = text.lower()
    if "从不吸烟" in text or "不吸烟" in text or "never smok" in lowered:
        facts["smoking_history"] = "never"
    elif "已戒" in text or "戒烟" in text or "former smok" in lowered:
        facts["smoking_history"] = "former"
    elif "吸烟" in text or "current smok" in lowered:
        facts["smoking_history"] = "current"
    m = _PACKYEARS_RE.search(text)
    if m and facts.get("smoking_history"):
        facts["smoking_history"] += f" ({m.group(1)} pack-years)"

    if "不可切除" in text or "unresectable" in lowered:
        facts["resectability_category"] = "UNRESECTABLE"
    elif "可切除" in text or "resectable" in lowered:
        facts["resectability_category"] = "RESECTABLE"

    if "腺鳞癌" in text or "adenosquamous" in lowered:
        facts["histologic_category"] = "adenosquamous"
    elif "腺癌" in text or "adenocarcinoma" in lowered:
        facts["histologic_category"] = "adenocarcinoma"
    elif "鳞癌" in text or "鳞状细胞癌" in text or "squamous" in lowered:
        facts["histologic_category"] = "squamous"
    return facts


_EXTRACTOR_SYSTEM = f"""=== {EXTRACTION_MARKER} ===
你是会诊系统的事实抽取器。从这条消息里抽取患者陈述的结构化临床事实。

只允许这些顶层键（其余一律不要输出）：
{json.dumps(sorted(ALLOWED_FACT_KEYS), ensure_ascii=False)}

规则：
- 只抽取消息**明确陈述**的内容，不推断、不诊断、不建议。
- driver_mutations 的值用消息原话片段；pd_l1 用数值。
- 只输出一个 JSON 对象：{{"facts": {{...}}}}。没有可抽取的就输出 {{"facts": {{}}}}。
- 永远不要输出 tumor_board_review、physician_review、signature 或任何以下划线开头的键。"""


class FactExtractor:
    """Allowlisted extraction: deterministic regexes, plus an optional model
    pass whose output is filtered through the same allowlist and validators."""

    def __init__(self, llm: Any | None = None) -> None:
        self.llm = llm

    def extract(self, message: str) -> tuple[dict[str, Any], list[str]]:
        notes: list[str] = []
        facts = extract_facts_deterministic(message)
        if self.llm is not None and getattr(self.llm, "available", False):
            model_facts = self._model_pass(message)
            for key, value in (model_facts or {}).items():
                facts.setdefault(key, value)
        cleaned, dropped = sanitize_fact_payload(facts)
        notes.extend(dropped)
        return cleaned, notes

    def _model_pass(self, message: str) -> dict[str, Any] | None:
        try:
            response = self.llm.chat(
                [{"role": "system", "content": _EXTRACTOR_SYSTEM},
                 {"role": "user", "content": message[:4000]}],
                temperature=0.0, max_tokens=600, response_format_json=True)
        except (LLMError, Exception):  # noqa: BLE001 - extraction is optional
            return None
        payload = extract_json(response.text, None)
        if not isinstance(payload, dict):
            return None
        facts = payload.get("facts")
        return facts if isinstance(facts, dict) else None


def sanitize_fact_payload(
    payload: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    """Enforce the allowlist and validate the values.

    Returns ``(cleaned, notes)``. Blocked or unknown keys are dropped loudly;
    TNM descriptors are engine-validated (a bare "N2" is refused here exactly
    as everywhere else); enums are range-checked.
    """
    cleaned: dict[str, Any] = {}
    notes: list[str] = []
    for key, value in (payload or {}).items():
        key = str(key)
        if key.startswith("_") or key in BLOCKED_FACT_KEYS:
            notes.append(
                f"CHAT_FACT_BLOCKED: {key!r} cannot be set through chat "
                f"(sign-off and guard keys have their own pathways)")
            continue
        if key not in ALLOWED_FACT_KEYS:
            notes.append(f"CHAT_FACT_IGNORED: unknown key {key!r}")
            continue
        if value is None:
            # A null never enters the record — and can therefore never
            # count as "restating" (confirming) a proposed value.
            notes.append(f"CHAT_FACT_IGNORED: {key} is null")
            continue
        if key == "ecog_ps":
            try:
                value = int(value)
            except (TypeError, ValueError):
                notes.append(f"CHAT_FACT_IGNORED: ecog_ps {value!r} not an int")
                continue
            if not 0 <= value <= 4:
                notes.append(f"CHAT_FACT_IGNORED: ecog_ps {value} out of range")
                continue
        if key == "tnm":
            if not isinstance(value, dict):
                notes.append("CHAT_FACT_IGNORED: tnm must be an object")
                continue
            validated: dict[str, Any] = {}
            normalizers = {"t": _normalize_t, "n": _normalize_n, "m": _normalize_m}
            ok = True
            for kind, normalize in normalizers.items():
                raw = value.get(kind)
                if raw is None:
                    continue
                try:
                    validated[kind] = normalize(str(raw))
                except StagingError as exc:
                    notes.append(f"CHAT_FACT_REFUSED[{kind.upper()}]: {exc}")
                    ok = False
            if not ok or not validated:
                continue
            if value.get("prefix"):
                validated["prefix"] = str(value["prefix"])
            value = validated
        if key in ("driver_mutations", "pd_l1", "comorbidities",
                   "organ_function"):
            if not isinstance(value, dict):
                notes.append(f"CHAT_FACT_IGNORED: {key} must be an object")
                continue
            # Keys coerced to str (mixed key types break canonical JSON and
            # every downstream consumer expects strings); nulls and junk
            # dropped per entry so a "confirmation" carrying garbage cannot
            # touch — and thereby clear — a proposed-fact guard entry.
            sub_cleaned: dict[str, Any] = {}
            for sub_key, sub_value in value.items():
                sub_key = str(sub_key)
                path = f"{key}.{sub_key}"
                if sub_value is None:
                    notes.append(f"CHAT_FACT_IGNORED: {path} is null")
                    continue
                if key == "pd_l1" and sub_key in ("tps", "tc", "ic", "cps"):
                    try:
                        sub_value = int(sub_value)
                    except (TypeError, ValueError):
                        notes.append(
                            f"CHAT_FACT_IGNORED: {path} {sub_value!r} "
                            f"is not a percentage")
                        continue
                    if not 0 <= sub_value <= 100:
                        notes.append(
                            f"CHAT_FACT_IGNORED: {path} {sub_value} "
                            f"out of range 0-100")
                        continue
                if key == "driver_mutations" and (
                        not isinstance(sub_value, str)
                        or not sub_value.strip()):
                    notes.append(
                        f"CHAT_FACT_IGNORED: {path} must be a non-empty "
                        f"result string")
                    continue
                sub_cleaned[sub_key] = sub_value
            if not sub_cleaned:
                continue
            value = sub_cleaned
        if key == "medications" and not isinstance(value, list):
            value = [str(value)]
        cleaned[key] = value
    return cleaned, notes


def merge_facts(
    target: dict[str, Any],
    incoming: dict[str, Any],
    *,
    overwrite: bool,
) -> tuple[list[str], list[str]]:
    """Merge sanitized facts into the session facts.

    Free-text extraction fills gaps only (``overwrite=False``): the narrative
    is hearsay-grade and must never displace an explicit record. Explicit
    structured facts from the operator overwrite (``overwrite=True``) — and
    when they land on a field the perception layer had only *proposed*, that
    field's ``_report_proposed`` guard entry is cleared: a human restating the
    value IS the confirmation step.

    Returns ``(changed_paths, conflict_notes)``.
    """
    changed: list[str] = []
    conflicts: list[str] = []
    #: Every path the payload addressed, changed or not — an operator
    #: restating a proposed value *unchanged* is still a confirmation.
    touched: list[str] = []
    proposed: list[str] = target.get("_report_proposed") or []

    def settle(path: str, container: dict, key: str, value: Any) -> None:
        if value is None:
            # Nulls neither write nor "touch": sanitize already drops them,
            # and a direct caller's null must not confirm a proposed fact.
            return
        touched.append(path)
        current = container.get(key)
        if current is None or current == value:
            if current != value:
                container[key] = value
                changed.append(path)
            return
        if overwrite:
            container[key] = value
            changed.append(path)
        else:
            conflicts.append(
                f"CHAT_FACT_CONFLICT[{path}]: narrative says {value!r} but the "
                f"record says {current!r} — the record stands; restate it as a "
                f"structured fact to change it")

    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            for sub, sub_value in value.items():
                settle(f"{key}.{sub}", target[key], sub, sub_value)
        else:
            if isinstance(value, dict) and key not in target:
                target[key] = {}
                for sub, sub_value in value.items():
                    settle(f"{key}.{sub}", target[key], sub, sub_value)
            else:
                settle(key, target, key, value)

    if overwrite and proposed and touched:
        confirmed = [p for p in proposed if p in touched]
        if confirmed:
            target["_report_proposed"] = [
                p for p in proposed if p not in confirmed]
            conflicts.append(
                "PROPOSED_FACT_CONFIRMED: operator restated "
                + ", ".join(confirmed)
                + " — cleared from the unconfirmed-proposal guard")
    return changed, conflicts


# --------------------------------------------------------------------------
# Reply composition — deterministic first, polish optional and guarded
# --------------------------------------------------------------------------

_POLISH_SYSTEM = """你是会诊系统的表达润色器。把给你的已放行回复改写得更通顺、更贴合
对话语气。硬性约束：不得新增任何临床内容、数值、药名或建议；不得删除问题或警示；
不得出现任何剂量数值。只输出改写后的文本。"""


def compose_reply(state: CaseRunState, *, role: str,
                  plan_reused: bool = False) -> str:
    """Deterministic reply: assembled from released outputs only."""
    if state.release_status == "emergency_action_plan":
        # The fixed script, verbatim — never rephrased, never summarized.
        plan = state.outputs.get("emergency_plan") or {}
        lines = ["⚠️ 肿瘤急症处理路径（固定安全脚本）："]
        lines += [f"• {a}" for a in plan.get("immediate_actions", [])]
        lines += ["不要做 / Do not:"] + [f"• {d}" for d in plan.get("do_not", [])]
        lines += ["立即升级如果 / Escalate if:"] + [
            f"• {e}" for e in plan.get("escalate_if", [])]
        return "\n".join(lines)

    parts: list[str] = []
    staging = state.staging or {}
    if staging.get("stage_group"):
        parts.append(
            f"分期：{staging['stage_group']}"
            f"（{staging.get('edition', '')}，确定性引擎计算）")
    plan = state.outputs.get("treatment_plan") or {}
    if plan.get("summary"):
        prefix = "（沿用上一轮方案，本轮无新决策事实）" if plan_reused else ""
        parts.append(f"{prefix}{plan['summary']}")
    for option in (plan.get("options") or [])[:4]:
        name = option.get("name")
        rationale = option.get("rationale")
        if name:
            parts.append(f"• {name}" + (f" — {rationale}" if rationale else ""))
    workup = state.outputs.get("workup_plan") or {}
    for step in (workup.get("steps") or [])[:4]:
        parts.append(f"→ 待完善：{step.get('gap')}（{step.get('test')}）")
    important = [f for f in state.flags
                 if f.startswith(("REPORT_", "IMAGING_", "STAGE_MISMATCH"))]
    for flag in important[:4]:
        parts.append(f"⚑ {flag}")
    if state.open_questions:
        parts.append("为了继续，请回答：")
        parts += [f"{i}. {q}" for i, q in enumerate(state.open_questions, 1)]
    from .render import _STATUS_EXPLANATION

    parts.append(
        f"[{state.release_status}] "
        f"{_STATUS_EXPLANATION.get(state.release_status, '')}")
    if role == "patient":
        parts.append("（教学/研究用途；任何治疗决定请与您的主治团队确认。）")
    return "\n".join(p for p in parts if p)


def polish_reply(llm: Any, deterministic_text: str, *,
                 state: CaseRunState) -> tuple[str, bool]:
    """Optional model polish, contained:

    never for the emergency script, dose-scanned on the way out, and any
    failure falls back to the deterministic text. Returns (text, polished).
    """
    if state.release_status == "emergency_action_plan":
        return deterministic_text, False
    if llm is None or not getattr(llm, "available", False):
        return deterministic_text, False
    if not state.budget.reserve_llm():
        return deterministic_text, False
    try:
        response = llm.chat(
            [{"role": "system", "content": _POLISH_SYSTEM},
             {"role": "user", "content": deterministic_text[:6000]}],
            temperature=0.3, max_tokens=1200)
        state.budget.charge_llm_tokens(response.total_tokens)
    except (LLMError, Exception):  # noqa: BLE001
        state.budget.refund_llm()
        return deterministic_text, False
    text = (response.text or "").strip()
    if not text or response.truncated:
        return deterministic_text, False
    # The outgoing dose scan: a numeric the deterministic text did not carry
    # cannot be introduced by the polish.
    if DOSE_RE.search(text) and not DOSE_RE.search(deterministic_text):
        state.warn("reply polish discarded: it introduced a dose numeric")
        return deterministic_text, False
    return text, True


# --------------------------------------------------------------------------
# The session
# --------------------------------------------------------------------------

@dataclass
class TurnResult:
    reply: str
    state: CaseRunState
    view: dict[str, Any]
    plan_reused: bool = False
    polished: bool = False
    extracted_facts: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    duration_s: float = 0.0
    llm_calls: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "reply": self.reply,
            "release_status": self.state.release_status,
            "plan_reused": self.plan_reused,
            "polished": self.polished,
            "extracted_facts": self.extracted_facts,
            "notes": self.notes,
            "duration_s": round(self.duration_s, 3),
            "llm_calls": self.llm_calls,
            "open_questions": list(self.state.open_questions),
            "view": self.view,
        }


class ConsultationSession:
    """A persistent multi-turn consultation over one case.

    Session memory (what makes later turns fast AND coherent):

    * one :class:`InterviewLoop` for the whole conversation — asked questions
      and stall history span turns;
    * accumulated narrative + accumulated validated facts;
    * refs of already-read films/reports (never re-read, never re-billed);
    * the previous treatment plan + its decision fingerprint (pure-question
      turns reuse it and skip the treatment tool-loop entirely);
    * the ``_report_proposed`` guard, carried across turns so the dose
      channel stays shut until a human confirms the proposed facts.
    """

    def __init__(
        self,
        *,
        llm: Any | None = None,
        vision_llm: Any | None = None,
        role: str = "patient",
        tools: Any | None = None,
        skill_registry: Any | None = None,
        checkpoint_dir: str | Path | None = None,
        parallel_tasks: bool = True,
        panel_concurrency: int = 4,
        allow_dose_planning: bool = False,
        polish_replies: bool = False,
        case_base_dir: Path | None = None,
    ) -> None:
        self.llm = llm
        self.role = role
        self.allow_dose_planning = allow_dose_planning
        self.polish_replies = polish_replies
        self.interview_loop = InterviewLoop(llm)
        self.extractor = FactExtractor(llm)
        self.runner = NSCLCRunner(
            tools=tools, llm=llm, vision_llm=vision_llm,
            skill_registry=skill_registry, checkpoint_dir=checkpoint_dir,
            parallel_tasks=parallel_tasks, panel_concurrency=panel_concurrency,
            interview_loop=self.interview_loop, case_base_dir=case_base_dir,
        )
        self.narrative: list[str] = []
        self.facts: dict[str, Any] = {}
        self.read_refs: set[str] = set()
        self.turns: list[TurnResult] = []
        self._plan_cache: dict[str, Any] | None = None
        self.last_state: CaseRunState | None = None

    # ------------------------------------------------------------------- turn
    def turn(
        self,
        message: str,
        *,
        images: list[str] | None = None,
        reports: list[str] | None = None,
        facts: dict[str, Any] | None = None,
        enable_panel: bool = False,
        allow_dose_planning: bool | None = None,
        polish: bool | None = None,
    ) -> TurnResult:
        started = time.monotonic()
        notes: list[str] = []
        message = (message or "").strip()
        if message:
            self.narrative.append(message)

        # 1. Fact intake: free text fills gaps; explicit facts overwrite (and
        #    confirm proposals); both pass the same allowlist + validators.
        extracted, extraction_notes = self.extractor.extract(message)
        notes.extend(extraction_notes)
        changed, conflicts = merge_facts(self.facts, extracted, overwrite=False)
        notes.extend(conflicts)
        if facts:
            explicit, explicit_notes = sanitize_fact_payload(facts)
            notes.extend(explicit_notes)
            explicit_changed, explicit_conflicts = merge_facts(
                self.facts, explicit, overwrite=True)
            changed.extend(explicit_changed)
            notes.extend(explicit_conflicts)

        # 2. New attachments only — an already-read film is session memory.
        new_images = [r for r in (images or []) if r not in self.read_refs]
        new_reports = [r for r in (reports or []) if r not in self.read_refs]

        # 3. Pure-question turns reuse the previous plan: nothing
        #    decision-relevant changed, so the expensive treatment loop is
        #    skipped and the critic re-audits the cached plan instead.
        tnm = dict(self.facts.get("tnm") or {})
        case = Case(
            t=tnm.get("t"), n=tnm.get("n"), m=tnm.get("m"),
            tnm_prefix=str(tnm.get("prefix") or "c"),
            stage_group=self.facts.get("stage_group"),
            staging_system=str(self.facts.get("staging_system") or "AJCC9"),
            presentation="\n".join(self.narrative),
            images=new_images, reports=new_reports,
            facts={k: v for k, v in self.facts.items()
                   if k not in ("tnm", "stage_group", "staging_system")},
        )
        internal = {}
        if self.facts.get("_report_proposed"):
            internal["_report_proposed"] = list(self.facts["_report_proposed"])
        plan_cache = None
        if (self._plan_cache is not None and not changed
                and not new_images and not new_reports):
            plan_cache = dict(self._plan_cache)

        state = self.runner.run_case(
            case, role=self.role,
            allow_dose_planning=(self.allow_dose_planning
                                 if allow_dose_planning is None
                                 else allow_dose_planning),
            enable_panel=enable_panel,
            internal_facts=internal or None,
            plan_cache=plan_cache,
        )

        # 4. Session memory update from the audited run. Deep copy: each
        #    TurnResult keeps the state it was audited with — a later turn's
        #    merges must never retroactively rewrite turn N's record.
        self.last_state = state
        self.facts = copy.deepcopy(state.facts)
        # Mark refs as read only when their reader actually consumed them —
        # a failed or unavailable read stays retryable on the next turn
        # instead of being silently skipped forever.
        films_failed = any(
            f.startswith(("IMAGING_READ_FAILED", "NO_VISION_PROVIDER"))
            for f in state.flags)
        reports_failed = any(
            f.startswith(("REPORT_READ_FAILED", "NO_VISION_PROVIDER"))
            for f in state.flags)
        if new_images and not films_failed:
            self.read_refs.update(str(r) for r in new_images)
        if new_reports and not reports_failed:
            self.read_refs.update(str(r) for r in new_reports)
        plan = state.outputs.get("treatment_plan") or {}
        plan_reused = bool(plan.get("reused_from_previous_turn"))
        if plan and state.release_status not in ("failed_closed",):
            # Cache the evidence rows backing the plan's citations alongside
            # the plan itself: citation ids are ledger-local, so a reusing
            # run re-adds these rows to ITS ledger (same tool-declared
            # grades) and remaps — provenance travels, ids never do.
            from dataclasses import asdict

            try:
                self._plan_cache = {
                    "fingerprint": decision_fingerprint(state.facts),
                    "plan": plan,
                    "evidence": [
                        asdict(state.evidence[c])
                        for c in plan.get("citations") or []
                        if isinstance(c, str) and c in state.evidence
                    ],
                }
            except Exception as exc:  # noqa: BLE001
                # The cache is an optimization; a cache-build fault after a
                # fully audited run must never discard that run's result.
                self._plan_cache = None
                state.warn(f"plan cache not stored: {exc}")

        # 5. Compose the reply (deterministic), optionally polish (guarded),
        #    and dose-scan the outgoing text unconditionally.
        reply = compose_reply(state, role=self.role, plan_reused=plan_reused)
        polished = False
        if polish if polish is not None else self.polish_replies:
            reply, polished = polish_reply(self.llm, reply, state=state)
        if state.release_status not in ("draft_for_tumor_board",
                                        "emergency_action_plan") \
                and DOSE_RE.search(reply):
            # Belt and braces: released prose is dose-free by construction;
            # anything else is a bug surfaced, not shipped. Two exemptions:
            # the tumor-board draft carries its dose channel by design, and
            # the emergency script is verbatim-fixed and never model-touched
            # (polish refuses it) — if a future script revision carries a
            # steroid dose, redaction here would mangle a safety script.
            state.warn("outgoing reply carried a dose numeric — redacted")
            reply = DOSE_RE.sub("[剂量见确定性通道]", reply)

        result = TurnResult(
            reply=reply, state=state, view=render(state, self.role),
            plan_reused=plan_reused, polished=polished,
            extracted_facts=changed, notes=notes,
            duration_s=time.monotonic() - started,
            llm_calls=state.budget.used_llm_calls,
        )
        self.turns.append(result)
        return result
