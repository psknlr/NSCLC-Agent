"""Hooks: deterministic code that runs at fixed points of the agent loop.

Same idea as Claude Code's hooks (UserPromptSubmit / PostToolUse / Stop):
the harness observes and comments at well-defined moments, and the user
decides which hooks are on. In NSCLC-Agent every clinical safety net lives
here, and every hook is ADVISORY — it adds context for the model or raises
findings the model must weigh; none of them blocks, rewrites or withholds.

* ``user_prompt_submit`` — before the model sees a message
  (emergency screen, case-note seeding);
* ``post_tool_use`` — after each tool call
  (evidence ledger: what the agent actually looked up);
* ``stop`` — when the agent submits its consult
  (rule engine, stage consistency, citation provenance, dose provenance,
  number provenance, emergency addressed). Unanswered findings go back to
  the model once.

The provenance hooks read every free-text field of the consult and the
text of every tool result (:mod:`.provenance`).
"""

from __future__ import annotations

import re
import json
from dataclasses import dataclass, field
from typing import Any, Callable

from . import provenance

EVENTS = ("user_prompt_submit", "post_tool_use", "stop")

#: Hooks that compare the model's decisions with the deterministic kernel
#: (or write kernel-extracted facts into its notes). Off by default in full
#: autonomy; the safety alerts and provenance checks stay on.
KERNEL_HOOKS = frozenset({"fact_seed", "stage_consistency", "rule_review"})


@dataclass
class HookResult:
    context: str | None = None
    alert: dict[str, Any] | None = None
    findings: list[dict[str, Any]] = field(default_factory=list)
    #: The alert event's key: "emergency" or "injection".
    alert_kind: str = "emergency"


@dataclass
class Hook:
    name: str
    event: str
    title: str
    description: str
    fn: Callable[[dict[str, Any]], HookResult | None]
    default: bool = True
    title_en: str = ""
    description_en: str = ""

    def to_dict(self, enabled: bool, lang: str = "zh") -> dict[str, Any]:
        en = lang == "en"
        return {"name": self.name, "event": self.event,
                "title": (self.title_en or self.title) if en else self.title,
                "description": (self.description_en or self.description) if en
                else self.description,
                "enabled": enabled}


class HookRunner:
    def __init__(self, hooks: list[Hook], enabled: dict[str, bool] | None = None) -> None:
        self.hooks = hooks
        self.enabled = {h.name: bool((enabled or {}).get(h.name, h.default)) for h in hooks}

    def run(self, event: str, ctx: dict[str, Any]) -> list[tuple[Hook, HookResult]]:
        out = []
        for hook in self.hooks:
            if hook.event != event or not self.enabled.get(hook.name):
                continue
            try:
                result = hook.fn(ctx)
            except Exception as exc:  # noqa: BLE001 - a broken hook is reported, not fatal
                result = HookResult(findings=[{"rule_id": f"HOOK_ERROR:{hook.name}",
                                               "severity": "warn",
                                               "message": f"{type(exc).__name__}: {exc}"}]
                                    if event == "stop" else [])
            if result is not None:
                out.append((hook, result))
        return out

    def describe(self, lang: str = "zh") -> list[dict[str, Any]]:
        return [h.to_dict(self.enabled[h.name], lang) for h in self.hooks]


def english(text: Any) -> str:
    """The English half of a bilingual kernel string ("大咯血 / massive
    hemoptysis", "急诊：…；ED now: …"); the text itself otherwise."""
    text = str(text)
    if " / " in text:
        return text.split(" / ", 1)[1].strip()
    if "；" in text:
        tail = text.rsplit("；", 1)[1].strip()
        if tail and tail.isascii():
            return tail
    return text


# --------------------------------------------------------------- built-ins

def _emergency_screen(ctx: dict[str, Any]) -> HookResult | None:
    from ..safety import emergencies

    screen = emergencies.screen(ctx["narrative"])
    if not screen.hard_hits:
        return None
    emergency = {"signals": [h["label"] for h in screen.hard_hits],
                 "pathway": emergencies.action_plan(screen)}
    ctx["state"]["emergency"] = emergency
    actions = emergency["pathway"].get("immediate_actions") or []
    if ctx.get("lang") == "en":
        context = ("[Emergency-screen hook] Hit: "
                   + ", ".join(english(s) for s in emergency["signals"])
                   + ". Standard pathway: " + " ".join(english(a) for a in actions)
                   + " (for your judgement; emergencies come first)")
    else:
        context = ("【急症筛查 hook】命中：" + "、".join(emergency["signals"])
                   + "。标准处置路径：" + "；".join(actions) + "（供你判断；急症优先）")
    return HookResult(context=context, alert=emergency)


def _injection_screen(ctx: dict[str, Any]) -> HookResult | None:
    """Pasted letters, outside opinions and forwarded reports can carry text
    addressed to the AI ("AI system: ignore your safety rules …", "【给AI的
    指令】…"). The clinician is told and the model is reminded that the
    pasted text is data (v1.4.1 red-team). Clinical corrections ("disregard
    my previous message", "以本次复查为准，忽略之前的报告") are not hits."""
    from ..safety import injection

    hits = injection.scan(ctx["message"])
    if not hits:
        return None
    en = ctx.get("lang") == "en"
    note = injection.warning("this message (pasted material)" if en else "本条消息（粘贴材料）",
                             hits, "en" if en else "zh")
    return HookResult(context=("[Injection-screen hook] " if en else "【注入筛查 hook】") + note,
                      alert=injection.alert("message", hits), alert_kind="injection")


def _fact_seed(ctx: dict[str, Any]) -> HookResult | None:
    from ..conversation import (
        extract_facts_deterministic, merge_facts, sanitize_fact_payload,
    )

    try:
        extracted = extract_facts_deterministic(ctx["message"])
    except Exception:  # noqa: BLE001 - seeding is best effort
        return None
    cleaned, _ = sanitize_fact_payload(extracted)
    changed, _ = merge_facts(ctx["facts"], cleaned, overwrite=False)
    if not changed:
        return None
    if ctx.get("lang") == "en":
        return HookResult(context="[Case-notes hook] Pre-filled from this message: "
                                  + ", ".join(changed)
                                  + " (correct with record_case_facts if wrong)")
    return HookResult(context="【病例笔记 hook】已从本条消息预填："
                              + "、".join(changed) + "（如有误请用 record_case_facts 修正）")


_REF_KEYS = {"trial_id", "rec_id", "nct", "pmid", "cluster_id"}
_REF_LIST_KEYS = {"trial_ids", "trial_refs", "verifiable_refs", "evidence"}


def _collect_refs(value: Any, out: set[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in _REF_KEYS and isinstance(item, (str, int)):
                out.add(_norm_ref(item))
            elif key in _REF_LIST_KEYS and isinstance(item, list):
                out.update(_norm_ref(x) for x in item if isinstance(x, (str, int)))
            else:
                _collect_refs(item, out)
    elif isinstance(value, list):
        for item in value:
            _collect_refs(item, out)


def _norm_ref(value: Any) -> str:
    return re.sub(r"[\s\-_]", "", str(value)).upper()


def _evidence_ledger(ctx: dict[str, Any]) -> HookResult | None:
    """Observer: remember every reference a tool actually returned — in its
    structured fields and in its text — every number it returned, and every
    regimen whose library dosing was looked up."""
    result = ctx.get("result") or {}
    _collect_refs(result.get("data"), ctx["state"]["evidence_seen"])
    text = provenance.tool_text(result)
    ctx["state"]["evidence_seen"].update(provenance.reference_keys(text))
    ctx["state"].setdefault("numbers_seen", set()).update(provenance.numbers(text))
    if ctx.get("tool") == "regimen_dosing" and result.get("ok"):
        ctx["state"]["dosing_seen"].add(str((ctx.get("args") or {}).get("regimen_id")))
    return None


def _rule_review(ctx: dict[str, Any]) -> HookResult | None:
    return HookResult(findings=ctx["toolbox"].rule_findings(ctx["consult"]))


def _stage_consistency(ctx: dict[str, Any]) -> HookResult | None:
    finding = ctx["toolbox"].stage_finding(ctx["consult"], lang=ctx.get("lang", "zh"))
    return HookResult(findings=[finding]) if finding else None


def _citation_provenance(ctx: dict[str, Any]) -> HookResult | None:
    """A cited reference that no tool returned AND that no local source can
    resolve is a likely hallucination — say so. Read in the options'
    evidence lists and in every free-text field (v1.4.1 red-team: a trial
    named in the reply, a PMID in the assessment, "依据 LUNAR-7 研究" in a
    rationale all passed unread)."""
    from ..knowledge import trials as trial_lib

    seen = ctx["state"]["evidence_seen"]
    kg = None
    unresolved = []
    narrative = provenance.norm_ref(ctx.get("narrative") or "")
    for text in provenance.consult_texts(ctx["consult"]):
        for shown, key in provenance.references(text):
            if provenance.ref_seen(key, seen) or key in narrative \
                    or trial_lib.resolve_trial_id(shown):
                continue
            unresolved.append(shown)
    for option in ctx["consult"].get("options") or []:
        for ref in (option or {}).get("evidence") or []:
            norm = _norm_ref(ref)
            if not norm or norm in seen or trial_lib.resolve_trial_id(str(ref)):
                continue
            if norm.startswith("REC"):
                if kg is None:
                    from ..knowledge.guideline_kg import load_default

                    kg = load_default()
                if kg is not None and kg.available and kg.get(str(ref).strip()):
                    continue
            unresolved.append(str(ref))
    if not unresolved:
        return None
    refs = sorted(set(unresolved))
    message = ("These references were neither returned by a tool in this consult nor "
               "found in the trial registry or guideline KB: " + ", ".join(refs)
               + " — verify them with search_trials / citation_verify, or remove them."
               if ctx.get("lang") == "en" else
               "以下引用既未出现在本次工具结果中，也无法在试验注册表/指南知识库中找到："
               + "、".join(refs) + "——请用 search_trials / citation_verify 核实，或删除。")
    return HookResult(findings=[{"rule_id": "UNVERIFIED_CITATION", "severity": "warn",
                                 "message": message}])


def _dose_provenance(ctx: dict[str, Any]) -> HookResult | None:
    """Doses anywhere in the consult, each traced to the library dosing of
    a regimen that contains its drug (v1.4.1 red-team: a dose in the
    warnings or the assessment went unread; one regimen's lookup cleared
    another regimen's doses; a dose that contradicted the looked-up
    library dose passed)."""
    mentions = provenance.dose_mentions(provenance.consult_texts(ctx["consult"]))
    if not mentions:
        return None
    looked = ctx["state"]["dosing_seen"]
    missing: list[str | None] = []
    differs: list[str] = []
    for mention in mentions:
        drug = mention["drug"]
        if drug is None:
            if not looked:
                missing.append(None)
            continue
        library = provenance.library_numbers(looked, drug)
        if library is None:
            if drug not in missing:
                missing.append(drug)
        elif mention["numbers"] and not mention["numbers"] & library:
            differs.append(f"{drug} {mention['text']}")
    findings = []
    en = ctx.get("lang") == "en"
    named = [d for d in missing if d]
    if named:
        findings.append({
            "rule_id": "DOSE_NOT_FROM_LIBRARY", "severity": "warn",
            "message": "The answer gives doses for " + ", ".join(named) + ", but regimen_dosing "
                       "was not called in this consult for a regimen containing them; check the "
                       "library reference dose or remove the specific doses." if en else
                       "回答给出了 " + "、".join(named) + " 的剂量，但本次会诊未对含该药的方案调用 "
                       "regimen_dosing 核对方案库参考剂量；请核对或删除具体剂量。"})
    elif missing:
        findings.append({
            "rule_id": "DOSE_NOT_FROM_LIBRARY", "severity": "warn",
            "message": "The answer contains dose figures, but regimen_dosing was not called "
                       "in this consult to check the library reference dose; verify or "
                       "remove the specific doses." if en else
                       "回答含剂量数值，但本次会诊未调用 regimen_dosing 核对方案库参考剂量；"
                       "请核对或删除具体剂量。"})
    if differs:
        findings.append({
            "rule_id": "DOSE_DIFFERS_FROM_LIBRARY", "severity": "warn",
            "message": "These doses differ from the library reference dose regimen_dosing "
                       "returned in this consult: " + "; ".join(sorted(set(differs)))
                       + " — correct them, or give the reason (e.g. a dose modification) in "
                       "rule_responses." if en else
                       "以下剂量与本次 regimen_dosing 返回的方案库参考剂量不一致："
                       + "；".join(sorted(set(differs))) + "——请更正，或在 rule_responses "
                       "说明理由（如剂量调整）。"})
    if ctx.get("role") == "patient":
        findings.append({
            "rule_id": "DOSE_TO_PATIENT", "severity": "warn",
            "message": "A patient-facing answer contains specific doses; say instead that "
                       "the treating team will set the exact dose." if en else
                       "面向患者的回答含具体剂量；建议改为“具体剂量由主治团队确定”。"})
    return HookResult(findings=findings) if findings else None


def _number_provenance(ctx: dict[str, Any]) -> HookResult | None:
    """An outcome statistic (a hazard ratio; a survival, response or risk
    percentage; a median in months) that no tool returned and the case does
    not contain is unanchored — say so (v1.4.1 red-team: "5-year overall
    survival is about 87%" and a misquoted FLAURA hazard ratio passed)."""
    anchors = set(ctx["state"].get("numbers_seen") or ())
    anchors |= provenance.numbers(ctx.get("narrative") or "")
    anchors |= provenance.numbers(json.dumps(ctx.get("facts") or {}, ensure_ascii=False,
                                             default=str))
    loose = []
    for text in provenance.consult_texts(ctx["consult"]):
        for value, clause in provenance.statistics(text):
            if value not in anchors and clause not in loose:
                loose.append(clause)
    if not loose:
        return None
    quoted = "; ".join(f"“{c[:120]}”" for c in loose[:4])
    message = ("These outcome figures were not returned by any tool in this consult and "
               "are not in the case record: " + quoted + " — look them up (search_trials / "
               "citation_verify) and quote the source, or remove them."
               if ctx.get("lang") == "en" else
               "以下疗效/预后数字既未出现在本次工具结果中，也不在病例资料里：" + quoted
               + "——请用 search_trials / citation_verify 查证并注明出处，或删除。")
    return HookResult(findings=[{"rule_id": "UNANCHORED_NUMBER", "severity": "warn",
                                 "message": message}])


#: The reply declares there is no emergency.
_EMERGENCY_DISMISSED_RE = re.compile(
    r"\bnot\s+(?:an?\s+|really\s+)?(?:emergenc\w*|urgent|emergent)|\bnon-?(?:urgent|emergent)\b|"
    r"\bnothing\s+urgent|\bisn'?t\s+(?:an?\s+)?(?:emergenc\w*|urgent)|"
    r"\bno\s+(?:need|reason)\s+(?:for|to)\s+(?:\w+\s+){0,2}(?:urgent|emergency|ed\b|hospital|"
    r"immediate)|"
    r"非急症|不是急症|不属于急症|不算急症|不紧急|不急|无需急诊|不需要?急诊|不必急诊|无需紧急|不必紧急|"
    r"不用着急|不需要?立即|无需立即|并非急症", re.IGNORECASE)
#: Wording that puts an emergency first: an emergency/urgent word, or an
#: immediate action aimed at emergency care — not "start osimertinib
#: immediately" / "立即开始奥希替尼" (v1.4.1 red-team).
_EMERGENCY_FIRST_RE = re.compile(
    r"\bemergenc(?:y|ies)\b|\burgent(?:ly)?\b|\bemergent(?:ly)?\b|\bsame[- ]day\b|"
    r"\b(?:ED|ER|A&E)\b|\bcall\s+(?:911|999|112|120|an?\s+ambulance)|"
    r"\b(?:go|come|present|attend)\s+(?:to\s+)?(?:the\s+)?(?:emergency|hospital|ED\b|ER\b)|"
    r"\b(?:immediate(?:ly)?|right away|now|tonight|today)\b[^.;\n]{0,40}"
    r"(?:emergency|hospital|\bED\b|ambulance|admi(?:t|ssion)|assess|evaluat|MRI|CT\b|"
    r"bronchoscop|antibiotic|culture|dexamethasone|neurosurg|airway)|"
    r"急诊|急症|紧急|(?:立即|立刻|马上|尽快|即刻|当天|今天|今晚)\s*(?:去|前往|到|送|拨打|呼叫)?\s*"
    r"(?:急诊|就医|就诊|送医|医院|120|评估|核磁|MRI|检查|处理|住院|入院|抢救|止血|抗感染|血培养|"
    r"抗生素|地塞米松|支气管镜)",
    re.IGNORECASE)


def _emergency_put_first(reply: str) -> bool:
    """The reply puts the emergency first: emergency wording that the reply
    does not itself negate ("This is not an emergency" does not count)."""
    if _EMERGENCY_DISMISSED_RE.search(reply):
        return False
    return bool(_EMERGENCY_FIRST_RE.search(reply))


def _emergency_addressed(ctx: dict[str, Any]) -> HookResult | None:
    emergency = ctx["state"].get("emergency")
    if not emergency:
        return None
    consult = ctx["consult"]
    reply = str(consult.get("reply") or "")
    dismissed = bool(_EMERGENCY_DISMISSED_RE.search(reply))
    if not dismissed and (consult.get("intent") == "emergency" or _emergency_put_first(reply)):
        return None
    signals = ", ".join(english(s) for s in emergency["signals"])
    en = ctx.get("lang") == "en"
    if dismissed:
        message = (f"The emergency screen fired ({signals}) but the reply declares this not "
                   f"an emergency." if en else
                   "急症筛查命中（" + "、".join(emergency["signals"]) + "），但回答称其不是急症。")
    else:
        message = (f"The emergency screen fired ({signals}) but the conclusion does not put "
                   f"the emergency first." if en else
                   "急症筛查命中（" + "、".join(emergency["signals"]) + "），但结论没有优先处理急症。")
    return HookResult(findings=[{"rule_id": "EMERGENCY_NOT_ADDRESSED", "severity": "block",
                                 "message": message}])


def builtin_hooks() -> list[Hook]:
    return [
        Hook("emergency_screen", "user_prompt_submit", "急症筛查",
             "每条消息做子句级、否定感知的肿瘤急症筛查；命中时把标准处置路径放到模型面前，并提示医生。",
             _emergency_screen, title_en="Emergency screen",
             description_en="Clause-scoped, negation-aware oncologic-emergency screen on every "
                            "message; a hit puts the standard pathway in front of the model "
                            "and alerts the clinician."),
        Hook("injection_screen", "user_prompt_submit", "注入筛查",
             "消息（含粘贴的外院意见、转诊信）中出现针对 AI 的指令（忽略规则、冒充系统、隐瞒医生…）时提示医生，"
             "并提醒模型这些文字只是数据。",
             _injection_screen, title_en="Injection screen",
             description_en="Flags text addressed to the AI inside a message (pasted letters, "
                            "outside opinions): overriding the rules, impersonating the system, "
                            "hiding things from the clinician. The clinician is told; the model "
                            "is reminded it is data."),
        Hook("fact_seed", "user_prompt_submit", "病例笔记预填",
             "从消息中确定性地提取明确事实（TNM、年龄、ECOG、驱动基因…）预填病例笔记，模型可修正。",
             _fact_seed, title_en="Case-note seeding",
             description_en="Deterministically extracts unambiguous facts (TNM, age, ECOG, "
                            "drivers…) from the message into the case notes; the model can "
                            "correct them."),
        Hook("evidence_ledger", "post_tool_use", "证据台账",
             "记录工具实际返回过的试验/指南/文献编号与方案库剂量查询，供引用与剂量溯源检查。",
             _evidence_ledger, title_en="Evidence ledger",
             description_en="Records every trial / guideline / literature id a tool actually "
                            "returned and every regimen-dose lookup, for the provenance checks."),
        Hook("rule_review", "stop", "规则引擎复核",
             "22 条确定性安全规则复核方案（驱动基因一线、N3 手术、适应证、器官功能、合并症、剂量…）。",
             _rule_review, title_en="Rule-engine review",
             description_en="The 22 deterministic safety rules review the plan (driver-directed "
                            "first line, no surgery for N3, indications, organ function, "
                            "comorbidities, doses…)."),
        Hook("stage_consistency", "stop", "分期一致性",
             "模型给出的分期与 AJCC/UICC 第 9 版分期引擎不一致时提示。", _stage_consistency,
             title_en="Stage consistency",
             description_en="Flags a stage that differs from the AJCC/UICC 9th-edition "
                            "staging engine."),
        Hook("citation_provenance", "stop", "引用溯源",
             "方案引用的试验/指南既未经工具返回、也无法在本地注册表或知识库找到时提示（防幻觉引用）。",
             _citation_provenance, title_en="Citation provenance",
             description_en="Flags cited trials / guideline items that no tool returned and no "
                            "local registry or knowledge base can resolve (hallucinated "
                            "citations)."),
        Hook("dose_provenance", "stop", "剂量溯源",
             "回答（任一字段）含剂量数值但未查含该药方案的方案库剂量、与查到的参考剂量不一致，"
             "或面向患者给出剂量时提示。", _dose_provenance,
             title_en="Dose provenance",
             description_en="Flags dose figures (in any field) for a drug whose regimen's "
                            "library dosing was not looked up, doses that differ from the "
                            "library dose looked up, or doses addressed to a patient."),
        Hook("number_provenance", "stop", "数字溯源",
             "疗效/预后数字（HR、生存率、缓解率、中位月数）既非工具返回、也不在病例资料中时提示。",
             _number_provenance, title_en="Number provenance",
             description_en="Flags outcome figures (hazard ratios, survival or response "
                            "percentages, medians in months) that no tool returned and the "
                            "case does not contain."),
        Hook("emergency_addressed", "stop", "急症优先",
             "急症筛查命中而结论未优先处理急症时提示（高优先级）。", _emergency_addressed,
             title_en="Emergency first",
             description_en="Flags a conclusion that does not put a screened emergency first "
                            "(high priority)."),
    ]
