"""Deterministic treatment-plan safety rules — the checks that were prompts in
v0.1 and are code here.

Every rule takes the run's authoritative context — the engine-computed staging,
the structured case facts, and the *parsed* model plan — and returns typed
violations. The critic runs this engine unconditionally; a ``block`` violation
prevents release and can request a bounded repair loop. The model can add
issues; it can never remove one of these.

The rules encode boundaries the protocol modules state in prose:

* N3 disease is not surgical.
* Known EGFR/ALK alterations exclude perioperative/adjuvant immunotherapy.
* EGFR-mutated unresectable stage III consolidates with osimertinib (LAURA),
  not durvalumab (PACIFIC EGFR subgroup: no benefit).
* Durvalumab is never concurrent with chemoradiation (PACIFIC-2 negative).
* Thoracic RT is not dose-escalated past ~66 Gy (RTOG 0617: 74 Gy harmed OS).
* A regimen is used inside its trial's stage boundaries, or the plan must
  declare the extrapolation explicitly with justification.
* Stage 0 (AIS, Tis) receives no systemic therapy — MIA is T1mi → IA1.
* Stage IV with an actionable driver gets driver-directed first-line therapy.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from ..knowledge import regimens as regimen_lib
from ..knowledge.trials import PERIOP_ADJUVANT_IO_TRIALS, TRIALS_BY_ID, resolve_trial_id

#: Spelled-out quantities that precede a dose unit ("eighty milligrams").
_SPELLED_NUM = (
    r"(?:one|two|three|four|five|six|seven|eight|nine|ten|twenty|thirty|forty|"
    r"fifty|sixty|seventy|eighty|ninety|hundred|thousand)(?:[- ]\w+)?"
)

#: Dose patterns a *model* output may never contain. Doses enter plans only
#: through the deterministic regimen library. Covers digit and spelled-out
#: quantities against mg/µg/IU-class units (red-team hardened: "milligrams",
#: "mcg", "μg", "IU" all count).
_DOSE_UNIT = (r"(?:mg/m2|mg/m²|mg/kg|mgs?\b|milligrams?\b|micrograms?\b|"
              r"mcg\b|[μµu]g\b|iu\b|毫克|g\b|克|c?Gy\b|戈瑞)")
#: v0.7.1 audit hardening: whole ranges ("45 to 50.4 Gy", "(60~66) Gy")
#: are one match so a scrub never leaves a bound behind; a closing bracket
#: or hyphen may sit between number and unit ("74-Gy", "200-mg",
#: "66) Gy"); "AUC=5" and "mgs"/"ug"/"cGy" count. Text is NFKC-normalized
#: before scanning, so full-width "２００ｍｇ" and "㎎" are caught too.
_DOSE_PATTERN = re.compile(
    r"(?:\d+(?:\.\d+)?\s*(?:[-‐–—~〜～]|to|至|到)\s*[(\[（]?\s*)?"
    r"\d+(?:\.\d+)?\s*[)\]）】〕]?\s*[-‐–—]?\s*" + _DOSE_UNIT
    + rf"|{_SPELLED_NUM}\s+(?:mg\b|milligrams?\b|micrograms?\b|mcg\b|grays?\b)"
    r"|AUC\s*[=:≈~]?\s*\d",
    re.IGNORECASE,
)


def normalize_for_scan(text: str) -> str:
    """NFKC: full-width digits/letters and unit ligatures ("㎎") become
    their ASCII forms before any dose scan."""
    return unicodedata.normalize("NFKC", str(text))


class _DoseScanner:
    """``DOSE_RE`` with NFKC normalization built in — every caller (rule
    engine, tool loop, interview loop, reply scan, KG scrub) gets the
    same hardened behavior through the same object."""

    pattern = _DOSE_PATTERN.pattern

    def search(self, text: str):
        return _DOSE_PATTERN.search(normalize_for_scan(text))

    def sub(self, repl: str, text: str) -> str:
        return _DOSE_PATTERN.sub(repl, normalize_for_scan(text))

    def finditer(self, text: str):
        return _DOSE_PATTERN.finditer(normalize_for_scan(text))


DOSE_RE = _DoseScanner()


def redact_doses(text: str, replacement: str) -> str:
    """Replace dose numerics in authored text, leaving library regimen ids
    ("ccrt_60gy") intact."""
    protected: dict[str, str] = {}
    out = str(text)
    for i, rid in enumerate(sorted(regimen_lib.REGIMENS_BY_ID, key=len,
                                   reverse=True)):
        if rid in out:
            token = f"\x00R{i}\x00"
            protected[token] = rid
            out = out.replace(rid, token)
    out = DOSE_RE.sub(replacement, out)
    for token, rid in protected.items():
        out = out.replace(token, rid)
    return out


def dose_in_payload(payload: Any) -> bool:
    """True when authored content carries a dose numeric. Library regimen
    ids are references into the deterministic library, not authored
    numerics ("ccrt_60gy"), so they are removed before scanning."""
    blob = payload if isinstance(payload, str) else json.dumps(
        payload, ensure_ascii=False)
    for rid in sorted(regimen_lib.REGIMENS_BY_ID, key=len, reverse=True):
        blob = blob.replace(rid, "")
    return bool(DOSE_RE.search(blob))

#: Surgery as the proposed management. Two layers: explicit procedure names
#: (any mention blocks under N3) and generic surgery words only in a
#: recommendation context, so "unresectable — resection not indicated" does
#: not false-positive.
_SURGERY_RE = re.compile(
    r"lobectomy|pneumonectomy|segmentectomy|wedge resection|sleeve resection|"
    r"surgical (?:resection|excision)|resection as primary|upfront surgery|"
    r"r0 resection|vats\b|肺叶切除|全肺切除|肺段切除|楔形切除|袖式切除|"
    r"手术切除|手术根治|根治性切除|直接手术|"
    r"(?:(?<![不没])recommend|proceed (?:to|with)|perform|undergo|offer|"
    r"take .{0,20}to (?:the )?(?:or\b|operating room)|(?<![不没])建议行?)"
    r"[^.。;；]{0,30}(?:resection|surgery|切除|手术|tumor removal)",
    re.IGNORECASE,
)

#: Concurrent durvalumab + (chemo)radiation, detected by proximity rather
#: than one phrasing: a durvalumab mention within a clause containing both a
#: concurrency word and a radiation word (red-team hardened: "together with",
#: "during", "alongside", 同步/同期 without a fixed suffix).
_DURVA_RE = re.compile(r"durvalumab|度伐利尤|度伐鲁", re.IGNORECASE)
_CONCURRENCY_RE = re.compile(
    r"concurrent|simultaneous|together with|alongside|during|combined with|同期|同步",
    re.IGNORECASE,
)
_RADIATION_RE = re.compile(
    r"radiation|radiotherapy|chemoradi|\bc?crt\b|\brt\b|放疗|放化疗", re.IGNORECASE,
)
_CLAUSE_RE = re.compile(r"[^.。;；\n]+")
#: Sequence words split a clause into steps: "concurrent chemoradiation,
#: then durvalumab" gives durvalumab AFTER the concurrent step, not with it.
_SEQUENCE_RE = re.compile(
    r"\bthen\b|followed by|thereafter|subsequently|\bafter\b|随后|之后|然后|序贯",
    re.IGNORECASE,
)
#: "Concurrent chemoradiation" is the name of a treatment (cCRT), not a
#: statement that durvalumab is concurrent with it.
_CCRT_NAME_RE = re.compile(
    r"concurrent\s+(?:chemo-?radi\w*|radio-?chemo\w*|crt\b|chemotherapy\s+and\s+radi\w*)"
    r"|同步放化疗|同期放化疗",
    re.IGNORECASE,
)


def _string_leaves(value: Any) -> list[str]:
    """Every string leaf in a nested payload, each its own unit.

    Proximity checks must run per authored string, not over the serialized
    JSON: a JSON blob has no sentence punctuation, so unrelated fields would
    concatenate into one giant pseudo-clause and cross-field words would
    false-positive together.
    """
    out: list[str] = []
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for item in value.values():
            out.extend(_string_leaves(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            out.extend(_string_leaves(item))
    return out

#: Absolute RT doses (1–3 digits) and per-fraction arithmetic. Only explicit
#: multiplication ("2 Gy × 37") or "per fraction … N fractions" multiplies —
#: "60 Gy in 30 fractions" states a TOTAL of 60, never 60 × 30.
_RT_DOSE_RE = re.compile(
    r"(\d{1,3}(?:\.\d+)?)\s*[)\]）】〕]?\s*[-‐–—]?\s*Gy", re.IGNORECASE)
_RT_FRACTION_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*Gy\s*(?:per fraction|/fx|/fraction)\s*"
    r"[^.。;；]{0,20}?[×x*]?\s*(\d{1,2})\s*(?:fx|fractions?|次|分次)"
    r"|(\d+(?:\.\d+)?)\s*Gy\s*[×x*]\s*(\d{1,2})\b",
    re.IGNORECASE,
)
#: A dose mention inside a prohibition ("do NOT escalate to 74 Gy") is the
#: safety note itself, not a proposal.
_RT_NEGATED_RE = re.compile(
    r"(?:do not|don't|never|avoid|not to|without)\s[^.。;；]{0,30}$|"
    r"(?:不得|不要|不应|勿|避免)[^.。;；]{0,15}$",
    re.IGNORECASE,
)


#: A declared extrapolation must carry a real justification: a blank or
#: token string ("  ", "ok") declared LAURA onto stage IV and silenced
#: the stage-boundary block (v0.7.1 audit).
_MIN_JUSTIFICATION_CHARS = 20


def declared_extrapolation_trials(plan: dict[str, Any]) -> set[str]:
    """Trials the plan validly declares as extrapolations: resolvable,
    anchoring one of the plan's OWN regimens or trial refs, and carrying
    a substantive justification."""
    anchors: set[str] = set()
    for raw in plan.get("trial_refs") or []:
        resolved = resolve_trial_id(str(raw))
        if resolved:
            anchors.add(resolved)
    rids = [str(r).strip().lower() for r in plan.get("regimen_ids") or []]
    for option in plan.get("options") or []:
        if isinstance(option, dict):
            rids.extend(str(r).strip().lower()
                        for r in option.get("regimen_ids") or [])
    for rid in rids:
        regimen = regimen_lib.get(rid)
        if regimen:
            anchors.update(regimen.trial_ids)
    out: set[str] = set()
    for item in plan.get("extrapolations") or []:
        if not isinstance(item, dict) or not item.get("trial_id"):
            continue
        justification = str(item.get("justification") or "").strip()
        if len(justification) < _MIN_JUSTIFICATION_CHARS:
            continue
        resolved = resolve_trial_id(str(item["trial_id"]))
        if resolved and resolved in anchors:
            out.add(resolved)
    return out


@dataclass
class Violation:
    rule_id: str
    severity: str  # "block" | "warn"
    message: str
    #: Agent whose output should be recomputed if a repair loop is granted.
    repair_agent: str = "TreatmentAgent"

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id, "severity": self.severity,
            "message": self.message, "repair_agent": self.repair_agent,
        }


@dataclass
class PlanContext:
    """Everything the rule engine reads. Built once by the critic."""

    stage_group: str = ""
    n_category: str = ""
    facts: dict[str, Any] = field(default_factory=dict)
    plan: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------- derived views
    @property
    def plan_text(self) -> str:
        return json.dumps(self.plan, ensure_ascii=False) if self.plan else ""

    @property
    def regimen_ids(self) -> list[str]:
        """Every regimen id the plan proposes — the top-level list AND each
        option's own list — normalized (stripped, lower-cased). An id
        hidden only inside an option, or padded with whitespace, is
        audited exactly like a top-level one."""
        raw = list(self.plan.get("regimen_ids") or [])
        for option in self.plan.get("options") or []:
            if isinstance(option, dict):
                raw.extend(option.get("regimen_ids") or [])
        out: list[str] = []
        for rid in raw:
            norm = str(rid).strip().lower()
            if norm and norm not in out:
                out.append(norm)
        return out

    @property
    def trial_refs(self) -> list[str]:
        """Trial ids referenced by the plan (resolved through the alias map)."""
        refs: list[str] = []
        for raw in self.plan.get("trial_refs") or []:
            resolved = resolve_trial_id(str(raw))
            refs.append(resolved or str(raw))
        # Regimens carry their anchors too.
        for rid in self.regimen_ids:
            regimen = regimen_lib.get(rid)
            if regimen:
                refs.extend(regimen.trial_ids)
        return sorted(set(refs))

    @property
    def declared_extrapolations(self) -> set[str]:
        return declared_extrapolation_trials(self.plan)

    def driver_positive(self, *genes: str) -> bool:
        from ..knowledge.biomarkers import driver_positive

        return driver_positive(self.facts, *genes)

    def driver_status_known(self, gene: str) -> bool:
        from ..knowledge.biomarkers import gene_status

        return gene_status(self.facts, gene) != "unknown"


# --------------------------------------------------------------------- rules

#: Any surgical procedure noun inside a proposed OPTION. Under N3 every
#: non-negated one blocks — "induction chemoradiation followed by
#: surgery", "thoracotomy if downstaged", "refer for operative
#: management" all evaded the verb-anchored pattern (v0.7.1 audit).
_PROCEDURE_RE = re.compile(
    r"\bsurg\w*|\bresect\w*|\bthoracotom\w*|\boperati\w*|\btrimodal\w*|"
    r"\blobectom\w*|\bpneumonectom\w*|\bsegmentectom\w*|\bvats\b|"
    r"手术|切除|开胸",
    re.IGNORECASE,
)
#: Negation reaching a following word within the same clause.
_PRE_NEGATION_RE = re.compile(
    r"(?:\bno\b|\bnot\b|\bnon-?|\bnever\b|\bavoid\w*|\bwithout\b|"
    r"\binstead of\b|\brather than\b|\bunresectab\w*|contraindicat\w*|"
    r"不|无需|无|非|避免|禁忌|而非)[^.。;；,，]{0,30}$",
    re.IGNORECASE,
)
#: Negation that follows the word ("surgery is not indicated").
_POST_NEGATION_RE = re.compile(
    r"^[^.。;；,，]{0,25}?(?:not\s+(?:indicated|recommended|an option|"
    r"part|appropriate|standard|needed|required)|contraindicated|"
    r"withheld|excluded|不推荐|不适合|不宜|不考虑|禁忌)",
    re.IGNORECASE,
)


def _negated(text: str, start: int, end: int) -> bool:
    return bool(_PRE_NEGATION_RE.search(text[:start])
                or _POST_NEGATION_RE.search(text[end:]))


def _option_texts(ctx: "PlanContext", *, with_rationale: bool
                  ) -> list[tuple[dict, str]]:
    out = []
    for option in ctx.plan.get("options") or []:
        if not isinstance(option, dict):
            continue
        parts = [str(option.get("name") or "")]
        if with_rationale:
            parts.append(str(option.get("rationale") or ""))
        out.append((option, " — ".join(parts)))
    return out


def _rule_n3_no_surgery(ctx: PlanContext) -> list[Violation]:
    if ctx.n_category != "N3":
        return []
    proposed = any(
        not _negated(text, m.start(), m.end())
        for _option, text in _option_texts(ctx, with_rationale=True)
        for m in _PROCEDURE_RE.finditer(text))
    if proposed or any(
            "surgery" in rid or "perioperative" in rid or "neoadjuvant" in rid
            for rid in ctx.regimen_ids) or _SURGERY_RE.search(ctx.plan_text):
        return [Violation(
            "N3_NO_SURGERY", "block",
            "N3 disease (contralateral mediastinal / supraclavicular nodes) is "
            "unresectable; the plan proposes surgical resection. Definitive "
            "chemoradiation + consolidation is the curative-intent pathway.",
        )]
    return []


def _rule_driver_excludes_periop_io(ctx: PlanContext) -> list[Violation]:
    if not ctx.driver_positive("egfr", "alk"):
        # The periop-IO registration trials named EGFR/ALK exclusions;
        # other actionable drivers were unrepresented — warn, not block.
        from ..knowledge.biomarkers import first_line_actionable_drivers

        others = first_line_actionable_drivers(ctx.facts)
        if others and any(
            (regimen_lib.get(rid) or None) and regimen_lib.get(rid).contains_ici
            and regimen_lib.get(rid).setting in
            ("neoadjuvant", "perioperative", "adjuvant")
            for rid in ctx.regimen_ids
        ):
            names = ", ".join(d["gene"] for d in others)
            return [Violation(
                "DRIVER_EXCLUDES_PERIOP_IO", "warn",
                f"Perioperative/adjuvant immunotherapy with an actionable "
                f"driver on record ({names}): these populations were "
                f"absent/unrepresented in the periop-IO trials and ICI "
                f"benefit in driver-positive disease is doubtful — MDT "
                f"discussion required.",
            )]
        return []
    out: list[Violation] = []
    for rid in ctx.regimen_ids:
        regimen = regimen_lib.get(rid)
        if regimen and regimen.contains_ici and regimen.setting in (
            "neoadjuvant", "perioperative", "adjuvant",
        ):
            out.append(Violation(
                "DRIVER_EXCLUDES_PERIOP_IO", "block",
                f"Known EGFR/ALK alteration with perioperative/adjuvant "
                f"immunotherapy ({regimen.name}): these trials excluded "
                f"EGFR/ALK+ disease and retrospective IO benefit is absent — "
                f"use targeted adjuvant standards (osimertinib/alectinib).",
            ))
    for tid in ctx.trial_refs:
        if tid in PERIOP_ADJUVANT_IO_TRIALS and not any(
            v.rule_id == "DRIVER_EXCLUDES_PERIOP_IO" for v in out
        ):
            out.append(Violation(
                "DRIVER_EXCLUDES_PERIOP_IO", "block",
                f"Plan anchors to {tid} in an EGFR/ALK-positive case — that "
                f"trial excluded (or showed no benefit in) driver-positive disease.",
            ))
    return out


def _rule_egfr_iii_consolidation(ctx: PlanContext) -> list[Violation]:
    if ctx.stage_group not in ("IIIA", "IIIB", "IIIC"):
        return []
    if not ctx.driver_positive("egfr"):
        return []
    uses_durva = "durva_consolidation" in ctx.regimen_ids or "PACIFIC" in ctx.trial_refs
    uses_osi = ("osimertinib_consolidation" in ctx.regimen_ids
                or "LAURA" in ctx.trial_refs)
    if uses_durva and not uses_osi:
        return [Violation(
            "EGFR_III_CONSOLIDATION", "block",
            "EGFR-mutated unresectable stage III: consolidation should be "
            "osimertinib (LAURA, PFS HR 0.16), not durvalumab — the PACIFIC "
            "EGFR subgroup showed no clear benefit.",
        )]
    return []


def _concurrent_durvalumab(text: str) -> bool:
    return bool(_DURVA_RE.search(text) and _CONCURRENCY_RE.search(text)
                and _RADIATION_RE.search(text))


def _rule_no_concurrent_durvalumab(ctx: PlanContext) -> list[Violation]:
    # A clause fires when any one step of it (split at sequence words) puts
    # durvalumab with radiation, OR when it still does once "concurrent
    # chemoradiation" is read as a treatment name — so "cCRT, then
    # durvalumab" is clean while "durvalumab … alongside the remaining RT"
    # still fires whatever sequence words surround it.
    for leaf in _string_leaves(ctx.plan):
        for clause in _CLAUSE_RE.findall(leaf):
            if not (any(_concurrent_durvalumab(step) for step in _SEQUENCE_RE.split(clause))
                    or _concurrent_durvalumab(_CCRT_NAME_RE.sub("cCRT", clause))):
                continue
            return [Violation(
                "NO_CONCURRENT_DURVALUMAB", "block",
                "Durvalumab given concurrently with (chemo)radiation — "
                "PACIFIC-2 was negative; durvalumab is consolidation only, "
                "started after CRT.",
            )]
    return []


def _rule_rt_dose(ctx: PlanContext) -> list[Violation]:
    # The RT-escalation check deliberately scans the WHOLE plan including the
    # deterministic dose_plan: an escalated total is wrong wherever it lives.
    text = normalize_for_scan(
        json.dumps(ctx.plan, ensure_ascii=False)) if ctx.plan else ""
    for rid in sorted(regimen_lib.REGIMENS_BY_ID, key=len, reverse=True):
        text = text.replace(rid, "")
    totals: list[float] = []
    fraction_spans: list[tuple[int, int]] = []
    for match in _RT_FRACTION_RE.finditer(text):
        fraction_spans.append(match.span())
        if match.group(1) is not None:
            totals.append(float(match.group(1)) * float(match.group(2)))
        else:
            totals.append(float(match.group(3)) * float(match.group(4)))
    for match in _RT_DOSE_RE.finditer(text):
        # Skip per-fraction doses already counted through the arithmetic form,
        # and doses inside a prohibition — the caution is not the proposal.
        if any(start <= match.start() < end for start, end in fraction_spans):
            continue
        if _RT_NEGATED_RE.search(text[max(0, match.start() - 40):match.start()]):
            continue
        totals.append(float(match.group(1)))
    for total in totals:
        if total > 66:
            return [Violation(
                "NO_RT_DOSE_ESCALATION", "block",
                f"Thoracic RT dose {total:g} Gy exceeds the definitive standard "
                f"— RTOG 0617 showed 74 Gy *worsened* survival vs 60 Gy/30 fx.",
            )]
    return []


def _rule_trial_stage_boundary(ctx: PlanContext) -> list[Violation]:
    if not ctx.stage_group:
        return []
    out: list[Violation] = []
    declared = ctx.declared_extrapolations
    for tid in ctx.trial_refs:
        trial = TRIALS_BY_ID.get(tid)
        if trial is None or not trial.stage_groups:
            continue
        if ctx.stage_group in trial.stage_groups:
            continue
        # Edition-aware compatibility: most registry trials enrolled under
        # AJCC 7/8. A case whose 8th-edition group falls inside the trial's
        # stages is an EDITION MIGRATION (same descriptors, renamed group),
        # not a biological extrapolation — note it, don't block it.
        if trial.tnm_edition == 8:
            from ..staging.legacy8 import eighth_edition_group

            tnm = ctx.facts.get("tnm") or {}
            legacy = eighth_edition_group(tnm.get("t"), tnm.get("n"),
                                          tnm.get("m"))
            if legacy and legacy in trial.stage_groups:
                out.append(Violation(
                    "TRIAL_EDITION_MIGRATION", "warn",
                    f"{tid}: the case is 9th-edition {ctx.stage_group} but "
                    f"its descriptors map to {legacy} under the trial's "
                    f"8th-edition enrollment — a TNM edition migration, not "
                    f"an extrapolation. Keep the edition note in the plan.",
                ))
                continue
        if tid in declared:
            out.append(Violation(
                "TRIAL_STAGE_EXTRAPOLATION", "warn",
                f"{tid} applied outside its enrolled stages "
                f"({'/'.join(sorted(trial.stage_groups))}) for stage "
                f"{ctx.stage_group} — declared as an extrapolation; keep the "
                f"uncertainty statement in the delivered plan.",
            ))
        else:
            out.append(Violation(
                "TRIAL_STAGE_BOUNDARY", "block",
                f"{tid} is applied to stage {ctx.stage_group} but enrolled "
                f"{'/'.join(sorted(trial.stage_groups))}. Either choose a "
                f"stage-covering regimen or declare the extrapolation "
                f"explicitly with justification.",
            ))
    return out


def _rule_stage0_no_systemic(ctx: PlanContext) -> list[Violation]:
    if ctx.stage_group != "0":
        return []
    systemic = [rid for rid in ctx.regimen_ids
                if regimen_lib.get(rid) and regimen_lib.get(rid).setting
                in ("adjuvant", "neoadjuvant", "perioperative", "first_line")]
    if systemic or re.search(r"adjuvant (?:chemo|immuno)|辅助化疗|辅助免疫", ctx.plan_text, re.IGNORECASE):
        return [Violation(
            "STAGE0_NO_SYSTEMIC", "block",
            "Stage 0 (AIS, Tis): complete resection (often sublobar) is curative "
            "— adjuvant/systemic therapy has no role and adds only harm.",
        )]
    return []


#: First-line driver-directed regimens — the full actionable plane, not
#: just EGFR/ALK (red-team: ROS1+/RET+ with PD-L1 80% was released onto
#: pembrolizumab monotherapy with zero violations).
_TARGETED_FIRST_LINE = frozenset({
    "osimertinib_first_line", "osimertinib_chemo_first_line",
    "amivantamab_lazertinib", "amivantamab_chemo_first_line",
    "afatinib_uncommon_first_line", "lorlatinib_first_line",
    "repotrectinib_first_line", "selpercatinib_first_line",
    "capmatinib_first_line", "dabrafenib_trametinib_first_line",
    "larotrectinib_first_line",
})


def _rule_driver_first_line(ctx: PlanContext) -> list[Violation]:
    if ctx.stage_group not in ("IVA", "IVB"):
        return []
    from ..knowledge.biomarkers import first_line_actionable_drivers

    drivers = first_line_actionable_drivers(ctx.facts)
    if not drivers:
        return []
    targeted = any(rid in _TARGETED_FIRST_LINE for rid in ctx.regimen_ids)
    ici_first = any(
        (regimen_lib.get(rid) or None) and regimen_lib.get(rid).contains_ici
        and regimen_lib.get(rid).setting == "first_line"
        for rid in ctx.regimen_ids
    )
    if ici_first and not targeted:
        names = ", ".join(
            d["gene"] + (f" ({'/'.join(d['classes'])})" if d["classes"] else "")
            for d in drivers)
        from ..knowledge.sequencing import driver_therapy_progressed

        if driver_therapy_progressed(ctx.facts):
            # Line-aware: after documented progression ON THE DRIVER'S
            # TARGETED THERAPY this is no longer a first-line question —
            # but the ICI answer is still poor in driver-positive disease
            # (KEYNOTE-789), so it stays flagged. Progression on chemo
            # alone does not qualify: the TKI is still the next line.
            return [Violation(
                "DRIVER_FIRST_LINE", "warn",
                f"ICI-containing regimen in driver-positive disease "
                f"({names}) after documented progression: KEYNOTE-789 "
                f"answered the post-TKI chemo-IO question negatively for "
                f"EGFR — the chemo-only and driver-directed sequencing "
                f"options are the defaults; document the MDT rationale "
                f"if proceeding.",
            )]
        return [Violation(
            "DRIVER_FIRST_LINE", "block",
            f"Stage IV with an actionable driver on record ({names}): "
            f"first-line therapy should be driver-directed, not "
            f"(chemo-)immunotherapy — ICI efficacy is poor in "
            f"driver-positive disease regardless of PD-L1, and sequencing "
            f"ICI before a TKI raises toxicity. This applies to "
            f"EGFR/ALK/ROS1/RET/MET-ex14/BRAF-V600E/NTRK alike.",
        )]
    return []


def _rule_progression_same_drug(ctx: PlanContext) -> list[Violation]:
    """Re-proposing an agent the history says the disease progressed on:
    warn. Rechallenge exists (post-chemo intervals, resistance reversal)
    but it is never a silent default — the plan must own the rationale."""
    from ..knowledge.sequencing import progressed_drugs_in

    out: list[Violation] = []
    for rid in ctx.regimen_ids:
        hits = progressed_drugs_in(rid, ctx.facts)
        if hits:
            out.append(Violation(
                "PROGRESSION_SAME_DRUG", "warn",
                f"{rid} contains {', '.join(hits)} — the record documents "
                f"progression on this agent. Rechallenge is a deliberate, "
                f"justified strategy, not a default: state the rationale "
                f"(treatment-free interval, resistance re-testing) or "
                f"choose the sequencing option.",
            ))
    return out


#: Osimertinib-family regimens whose evidence populations are
#: ex19del/L858R (FLAURA/FLAURA2/MARIPOSA/ADAURA/LAURA).
_CLASSICAL_EGFR_REGIMENS = frozenset({
    "osimertinib_first_line", "osimertinib_chemo_first_line",
    "amivantamab_lazertinib", "osimertinib_adjuvant",
    "osimertinib_consolidation", "tepotinib_osimertinib_met_amp",
    "osimertinib_t790m_subsequent",
})


def _rule_egfr_variant_mismatch(ctx: PlanContext) -> list[Violation]:
    """The ontology-level guard the red-team asked for: an osimertinib-
    family regimen with an EGFR variant class outside its evidence
    population. Independent of the planner's own gating — a planner bug
    here must not survive the critic."""
    from ..knowledge.biomarkers import (
        EGFR_CLASSICAL_SENSITIZING, EGFR_UNCOMMON_SENSITIZING, egfr_classes,
    )

    used = [rid for rid in ctx.regimen_ids if rid in _CLASSICAL_EGFR_REGIMENS]
    if not used:
        return []
    classes = egfr_classes(ctx.facts)
    if not classes:
        return []  # BIOMARKER/driver rules handle the negative/unknown case
    label = "/".join(sorted(classes))
    if "exon20ins" in classes:
        return [Violation(
            "EGFR_VARIANT_MISMATCH", "block",
            f"EGFR {label} with {', '.join(used)}: exon 20 insertions are "
            f"NOT the FLAURA/ADAURA/LAURA population — osimertinib is not "
            f"standard for this variant class; first-line is amivantamab + "
            f"chemotherapy (PAPILLON).",
        )]
    if "c797s" in classes and not (classes & EGFR_CLASSICAL_SENSITIZING):
        return [Violation(
            "EGFR_VARIANT_MISMATCH", "block",
            f"EGFR {label}: C797S confers osimertinib resistance — "
            f"{', '.join(used)} is not an evidence-based choice here.",
        )]
    if classes & EGFR_CLASSICAL_SENSITIZING:
        return []
    if classes <= (EGFR_UNCOMMON_SENSITIZING | {"t790m"}):
        return [Violation(
            "EGFR_VARIANT_MISMATCH", "warn",
            f"EGFR {label} with {', '.join(used)}: outside the "
            f"ex19del/L858R trial populations — uncommon-sensitizing "
            f"alterations have a separate evidence base "
            f"(afatinib/osimertinib per pooled analyses); document the "
            f"variant-specific rationale.",
        )]
    return [Violation(
        "EGFR_VARIANT_MISMATCH", "warn",
        f"EGFR positive but variant unclassified ({label}) with "
        f"{', '.join(used)}: confirm the exact alteration and its "
        f"sensitizing status before committing to a classical-EGFR regimen.",
    )]


def _rule_indication_predicate(ctx: PlanContext) -> list[Violation]:
    """Every regimen in the plan is audited against its DECLARED
    population — the single set of declarations the planner also gated on,
    re-evaluated here so a model-authored (or buggy table) plan cannot
    carry an out-of-population regimen to release. Unknown facts warn with
    the missing item named; a stage-only failure covered by a declared
    extrapolation is left to the boundary rule's warn."""
    from ..knowledge.indications import (
        INELIGIBLE, UNKNOWN as IND_UNKNOWN, evaluate_indication,
    )

    out: list[Violation] = []
    declared_trials = ctx.declared_extrapolations
    for rid in ctx.regimen_ids:
        verdict = evaluate_indication(rid, ctx.stage_group, ctx.facts)
        if not verdict["declared"]:
            out.append(Violation(
                "INDICATION_UNDECLARED", "block",
                f"{rid} is not a declared library regimen — a plan may only "
                f"propose regimen ids from the library (whose populations "
                f"are machine-executable); an unknown id cannot be audited "
                f"and does not ship.",
            ))
            continue
        if verdict["verdict"] == INELIGIBLE:
            failed = verdict["failed_conditions"]
            stage_only = all(f.startswith("stage:") for f in failed)
            regimen = regimen_lib.get(rid)
            if stage_only and regimen \
                    and declared_trials & set(regimen.trial_ids):
                continue  # TRIAL_STAGE_EXTRAPOLATION already warns
            out.append(Violation(
                "INDICATION_PREDICATE", "block",
                f"{rid} fails its declared indication: "
                + "; ".join(failed),
            ))
        elif verdict["verdict"] == IND_UNKNOWN:
            out.append(Violation(
                "INDICATION_PREDICATE", "warn",
                f"{rid}: indication unresolved — missing facts: "
                + "; ".join(verdict["unknown_conditions"])
                + ". Unknown routes to workup, not to a guess.",
            ))
    return out


#: Systemic drugs a plan may name. Library drugs come from the regimen
#: components; the rest are real agents with no library regimen, so
#: naming one in an option can never be bound and always blocks.
_DRUG_SUFFIX_RE = re.compile(
    r"^[a-z]+(?:mab|nib|platin|trexed|taxel|poside|rasib|tecan|bine)$")
_EXTRA_DRUGS = (
    "gefitinib", "erlotinib", "dacomitinib", "icotinib", "aumolertinib",
    "furmonertinib", "crizotinib", "brigatinib", "ceritinib", "ensartinib",
    "entrectinib", "taletrectinib", "pralsetinib", "savolitinib",
    "encorafenib", "binimetinib", "adagrasib", "cemiplimab", "tislelizumab",
    "sintilimab", "camrelizumab", "toripalimab", "bevacizumab",
    "gemcitabine", "irinotecan",
)
_CHINESE_DRUGS = {
    "替雷利珠": "tislelizumab", "信迪利": "sintilimab",
    "卡瑞利珠": "camrelizumab", "特瑞普利": "toripalimab",
    "贝伐珠": "bevacizumab", "安罗替尼": "anlotinib",
}


def _drug_vocabulary() -> dict[str, str]:
    """Surface form → generic drug name."""
    from ..knowledge.sequencing import _AGENT_ALIASES

    vocab: dict[str, str] = {}
    for regimen in regimen_lib.REGIMENS:
        for component in regimen.components:
            for token in re.findall(r"[a-z]{5,}", component.drug.lower()):
                if _DRUG_SUFFIX_RE.match(token):
                    vocab[token] = token
    for drug in _EXTRA_DRUGS:
        vocab[drug] = drug
    vocab["anlotinib"] = "anlotinib"
    for alias, generic in {**_AGENT_ALIASES, **_CHINESE_DRUGS}.items():
        if _DRUG_SUFFIX_RE.match(generic) or generic in vocab:
            vocab[alias] = generic
    return vocab


_DRUGS = _drug_vocabulary()
_DRUG_MENTION_RE = re.compile(
    "|".join(sorted((re.escape(k) for k in _DRUGS), key=len, reverse=True)),
    re.IGNORECASE)


def _rule_option_drug_unbound(ctx: PlanContext) -> list[Violation]:
    """An option NAMED after a systemic drug must carry a library regimen
    containing that drug. Otherwise every regimen-based check (driver,
    predicate, variant, organ, claim entailment) is silently skipped —
    free-text "Pembrolizumab monotherapy" with empty regimen_ids on an
    EGFR+ case released with zero violations (v0.7.1 audit)."""
    out: list[Violation] = []
    for option, text in _option_texts(ctx, with_rationale=False):
        bound: set[str] = set()
        for rid in option.get("regimen_ids") or []:
            regimen = regimen_lib.get(str(rid).strip().lower())
            if regimen:
                bound.update(c.drug.lower() for c in regimen.components)
        unbound = sorted({
            _DRUGS[m.group(0).lower()] if m.group(0).lower() in _DRUGS
            else _DRUGS.get(m.group(0), m.group(0))
            for m in _DRUG_MENTION_RE.finditer(text)
            if not _negated(text, m.start(), m.end())
        } - {d for d in _DRUGS.values()
             if any(d in component for component in bound)})
        if unbound:
            out.append(Violation(
                "OPTION_DRUG_UNBOUND", "block",
                f"Option '{text[:70]}' names {', '.join(unbound)} but "
                f"carries no library regimen containing it — a drug named "
                f"in free text bypasses every regimen-level safety check; "
                f"bind it to a library regimen id or remove it.",
            ))
    return out


_CONSOLIDATION_AFTER_CRT = ("durva_consolidation", "osimertinib_consolidation")
_CRT_HISTORY_RE = re.compile(r"chemoradi|\bc?crt\b|放化疗|同步放化疗", re.I)


def _rule_consolidation_requires_crt(ctx: PlanContext) -> list[Violation]:
    """PACIFIC and LAURA consolidation exist only AFTER chemoradiation: a
    plan proposing consolidation with no chemoradiation in the plan and
    none in the treatment history has skipped the definitive therapy."""
    used = [r for r in ctx.regimen_ids if r in _CONSOLIDATION_AFTER_CRT]
    if not used or "ccrt_60gy" in ctx.regimen_ids:
        return []
    from ..knowledge.sequencing import treatment_history

    if any(_CRT_HISTORY_RE.search(agent)
           for entry in treatment_history(ctx.facts)
           for agent in entry["agents"]):
        return []
    return [Violation(
        "CONSOLIDATION_WITHOUT_CRT", "block",
        f"{', '.join(used)} is consolidation AFTER definitive chemoradiation "
        f"(PACIFIC/LAURA) — the plan has no chemoradiation and the history "
        f"records none.",
    )]


def _rule_organ_function(ctx: PlanContext) -> list[Violation]:
    """The population fits; does this BODY? A regimen whose organ or
    comorbidity gate FAILS on the recorded facts blocks at
    recommendation time — CrCl 38 does not take a pemetrexed backbone,
    a bilirubin above normal does not take docetaxel, a hemoptysis
    history does not take ramucirumab. Unknown never blocks here:
    recommending is not dosing, and the dose channel refuses unverified
    gates on its own."""
    from ..knowledge.organ_gates import failed_gates

    out: list[Violation] = []
    for rid in ctx.regimen_ids:
        for failure in failed_gates(rid, ctx.facts):
            out.append(Violation(
                "ORGAN_FUNCTION_GATE", "block",
                f"{rid}: {failure['note']} — choose a compatible backbone "
                f"or route to the MDT/pharmacist; the dose is never "
                f"'adjusted around' a failed gate at this layer.",
            ))
    return out


def _rule_ici_comorbidity(ctx: PlanContext) -> list[Violation]:
    comorbid = ctx.facts.get("comorbidities") or {}
    risky = comorbid.get("ild") or comorbid.get("active_autoimmune") \
        or comorbid.get("autoimmune_disease")
    if not risky:
        return []
    if any((regimen_lib.get(rid) or None) and regimen_lib.get(rid).contains_ici
           for rid in ctx.regimen_ids):
        return [Violation(
            "ICI_COMORBIDITY_CAUTION", "warn",
            "Checkpoint inhibitor planned with ILD / active autoimmune disease "
            "on record — document the risk-benefit discussion and monitoring "
            "plan; these patients were excluded from the registration trials.",
        )]
    return []


def _rule_ps_gate(ctx: PlanContext) -> list[Violation]:
    ecog = ctx.facts.get("ecog_ps")
    if not isinstance(ecog, int) or ecog < 3:
        return []
    aggressive = ("ccrt_60gy" in ctx.regimen_ids
                  or any("perioperative" in rid or "neoadjuvant" in rid
                         for rid in ctx.regimen_ids))
    if aggressive:
        return [Violation(
            "PS_GATE", "warn",
            f"ECOG PS {ecog} with concurrent CRT / perioperative therapy planned "
            f"— registration trials required PS 0–1 (PS 2 at most); document "
            f"the fitness rationale or de-escalate.",
        )]
    return []


def _rule_biomarker_before_systemic(ctx: PlanContext) -> list[Violation]:
    """Tier-A biomarkers must be known before committing systemic therapy."""
    if ctx.stage_group in ("0", "Occult", ""):
        return []
    histology = str(ctx.facts.get("histologic_category") or "").lower()
    planned_systemic = any(
        (regimen_lib.get(rid) or None) is not None
        and regimen_lib.get(rid).setting in
        ("adjuvant", "neoadjuvant", "perioperative", "first_line", "consolidation")
        for rid in ctx.regimen_ids
    )
    if not planned_systemic:
        return []
    if histology and "squamous" in histology and "adeno" not in histology:
        return []  # pure squamous: EGFR/ALK testing optional per guideline
    missing = [g for g in ("egfr", "alk") if not ctx.driver_status_known(g)]
    if missing:
        return [Violation(
            "BIOMARKER_GAP", "block",
            f"Systemic therapy committed with Tier-A biomarkers untested "
            f"({', '.join(g.upper() for g in missing)}): contemporary standard "
            f"requires EGFR/ALK (and PD-L1 where ICI is considered) before "
            f"finalizing — decisions are provisional pending testing.",
            repair_agent="TreatmentAgent",
        )]
    return []


def _rule_dose_scan(ctx: PlanContext) -> list[Violation]:
    """Model-authored fields may not carry dose numerics.

    The deterministic dose channel (``dose_plan`` output) is exempt — that is
    the *only* place numbers may live. Library identifiers (``ccrt_60gy``…)
    are scrubbed before scanning: an id is a reference into the deterministic
    library, not an authored numeric.
    """
    scrubbed = {k: v for k, v in ctx.plan.items() if k != "dose_plan"}
    if dose_in_payload(scrubbed):
        return [Violation(
            "DOSE_IN_MODEL_OUTPUT", "block",
            "A dose numeric appears in model-authored plan content. Doses enter "
            "plans only through the deterministic regimen library (dose_plan).",
        )]
    return []


#: Plan-text markers proving CNS-directed care is IN the plan. Broad on
#: purpose: the rule asks "was the CNS addressed at all", not "was it
#: addressed well" — sequencing quality is the MDT's judgment.
_CNS_LOCAL_MARKERS = (
    "srs", "stereotactic", "radiosurgery", "whole-brain", "wbrt",
    "neurosurg", "cns-directed", "neuro-oncology", "神经外科", "放射外科",
    "全脑", "脑局部", "神经肿瘤",
)


def _rule_cns_untreated_symptomatic(ctx: PlanContext) -> list[Violation]:
    """Symptomatic untreated brain metastases (or leptomeningeal disease)
    with a systemic-only plan: block. Independent of the planner's own
    stratification — a model-authored plan that ignores the CNS must not
    survive the critic."""
    from ..knowledge.cns import cns_status

    reading = cns_status(ctx.facts)
    if reading["status"] != "present" or not ctx.regimen_ids:
        return []
    dangerous = (reading["symptomatic"] and not reading["treated"]) \
        or bool(reading["leptomeningeal"])
    if not dangerous:
        return []
    # CNS-directed care must be an OPTION the plan offers: flagged
    # cns_directed, or NAMED with a CNS marker that is not negated ("SRS
    # not needed", "neuro-oncology consult not needed") and is not body
    # SBRT elsewhere. A marker in a rationale, an uncertainty, a workup
    # line or attached guideline context does not address the CNS.
    for option, name in _option_texts(ctx, with_rationale=False):
        if option.get("cns_directed") is True:
            return []
        lowered = name.lower()
        for marker in _CNS_LOCAL_MARKERS:
            for m in re.finditer(re.escape(marker), lowered):
                if marker == "stereotactic" and re.match(
                        r"stereotactic\s+body", lowered[m.start():]):
                    continue
                if not _negated(lowered, m.start(), m.end()):
                    return []
    label = ("leptomeningeal disease" if reading["leptomeningeal"]
             else "symptomatic untreated brain metastases")
    return [Violation(
        "CNS_UNTREATED_SYMPTOMATIC", "block",
        f"The record shows {label} but the plan proposes systemic therapy "
        f"with no CNS-directed component (no neurosurgery/radiation-"
        f"oncology/neuro-oncology involvement, no local-therapy option). "
        f"Address the CNS explicitly before any systemic-only release.",
    )]


def _rule_cns_tnm_consistency(ctx: PlanContext) -> list[Violation]:
    """CNS metastases on record while the descriptors say M0: name the
    contradiction; never repair it (the staging engine stays the only
    staging authority)."""
    from ..knowledge.cns import tnm_conflict

    conflict = tnm_conflict(ctx.facts)
    if conflict:
        return [Violation("CNS_TNM_INCONSISTENT", "warn", conflict)]
    return []


RULES = (
    _rule_n3_no_surgery,
    _rule_driver_excludes_periop_io,
    _rule_egfr_variant_mismatch,
    _rule_egfr_iii_consolidation,
    _rule_no_concurrent_durvalumab,
    _rule_rt_dose,
    _rule_trial_stage_boundary,
    _rule_stage0_no_systemic,
    _rule_indication_predicate,
    _rule_driver_first_line,
    _rule_option_drug_unbound,
    _rule_consolidation_requires_crt,
    _rule_progression_same_drug,
    _rule_cns_untreated_symptomatic,
    _rule_cns_tnm_consistency,
    _rule_organ_function,
    _rule_ici_comorbidity,
    _rule_ps_gate,
    _rule_biomarker_before_systemic,
    _rule_dose_scan,
)


def check_plan(
    staging: dict[str, Any],
    facts: dict[str, Any],
    plan: dict[str, Any],
) -> list[Violation]:
    """Run every rule; returns violations, blockers first."""
    ctx = PlanContext(
        stage_group=str(staging.get("stage_group") or ""),
        # Staging is authoritative, but a missing n_category must not
        # silently disarm the N3 rule when the descriptor sits right there
        # in the facts — found by the audit-type eval cases (the N3-surgery
        # probe passed the net because staging lacked the key).
        n_category=str(staging.get("n_category")
                       or ((facts or {}).get("tnm") or {}).get("n") or ""),
        facts=facts or {},
        plan=plan or {},
    )
    violations: list[Violation] = []
    for rule in RULES:
        violations.extend(rule(ctx))
    violations.sort(key=lambda v: 0 if v.severity == "block" else 1)
    return violations
