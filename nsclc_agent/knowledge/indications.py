"""Machine-executable indication predicates — one declaration per regimen.

The clinical red-team review's core structural recommendation: stop encoding
regimen eligibility as planner if/else plus separately-written safety regex,
and instead declare each regimen's population ONCE, machine-executably, and
evaluate that declaration everywhere a regimen is proposed or audited.

Three-valued logic, because clinical facts are often simply absent:

* ``eligible``   — every declared condition is met;
* ``ineligible`` — at least one condition is confidently NOT met;
* ``unknown``    — nothing failed, but a condition rests on a fact the case
  does not carry. **Unknown routes to workup, never to a guess**: the
  planner keeps the option only as provisional and asks for the missing
  fact; the critic warns with the missing fact named.

One evaluator, three call sites — the planner's ``opt()`` gate, the
attach-layer report (``outputs["indication_report"]``), and the critic rule
``INDICATION_PREDICATE``. The declarations are the shared source of truth;
what stays independent is *what each caller does* with a verdict (drop /
annotate / block). Stage conditions are TNM-edition-aware through the same
8th-edition back-mapping the boundary rule uses, so an edition migration is
an annotation and never a false ``ineligible``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..staging.legacy8 import eighth_edition_group
from .biomarkers import (
    EGFR_UNCOMMON_SENSITIZING,
    _BRAF_V600_RE,
    _MET_EX14_RE,
    _normalized_drivers,
    driver_status,
    egfr_classes,
    egfr_classical,
    first_line_actionable_drivers,
)

ELIGIBLE = "eligible"
INELIGIBLE = "ineligible"
UNKNOWN = "unknown"

_MET = "met"
_NOT_MET = "not_met"
_UNKNOWN = "unknown"


@dataclass(frozen=True)
class Indication:
    """Declared population for one regimen. Absent fields mean "no
    requirement" — a predicate states what the evidence requires, nothing
    more."""

    regimen_id: str
    #: 9th-edition stage groups where use is on-evidence.
    stage_groups: frozenset[str]
    #: Edition the underlying trials enrolled under (for back-mapping).
    tnm_edition: int = 8
    histology: str = "any"  # any | nonsquamous | squamous
    #: Required driver gene (normalized key: egfr/alk/ros1/ret/met/braf/
    #: ntrk/erbb2), optionally refined by class.
    driver: str | None = None
    driver_class: str | None = None  # egfr_classical | egfr_exon20ins |
    #                                  egfr_uncommon | met_ex14 | braf_v600e
    #: ICI-first regimens: no first-line actionable driver may be present.
    requires_no_actionable_driver: bool = False
    pd_l1_tps_ge: float | None = None
    pd_l1_tc_ge: float | None = None
    resectability: str | None = None  # RESECTABLE | UNRESECTABLE
    requires_inoperable: bool = False
    requires_oligometastatic: bool = False
    requires_prior_systemic: bool = False
    note: str = ""


def _cond(name: str, requirement: str, verdict: str, why: str) -> dict[str, Any]:
    return {"condition": name, "requirement": requirement,
            "verdict": verdict, "why": why}


# ------------------------------------------------------------ evaluators

def _eval_stage(ind: Indication, stage_group: str | None,
                facts: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    requirement = "/".join(sorted(ind.stage_groups))
    if not stage_group:
        return _cond("stage", requirement, _UNKNOWN,
                     "case stage not computed"), False
    stage = str(stage_group).upper()
    if stage in ind.stage_groups:
        return _cond("stage", requirement, _MET, f"stage {stage}"), False
    if ind.tnm_edition == 8:
        tnm = facts.get("tnm") or {}
        legacy = eighth_edition_group(tnm.get("t"), tnm.get("n"), tnm.get("m"))
        if legacy and legacy in ind.stage_groups:
            return _cond(
                "stage", requirement, _MET,
                f"9th-edition {stage} maps to {legacy} under the trial's "
                f"8th-edition enrollment (edition migration)"), True
    return _cond("stage", requirement, _NOT_MET,
                 f"stage {stage} outside {requirement}"), False


def _eval_histology(ind: Indication, facts: dict[str, Any]) -> dict[str, Any]:
    from .kg_eligibility import _classify_histology

    case = _classify_histology(str(facts.get("histologic_category") or ""))
    if ind.histology == "nonsquamous":
        if case in ("adenocarcinoma", "non_squamous"):
            return _cond("histology", "nonsquamous", _MET, str(case))
        if case == "squamous":
            return _cond("histology", "nonsquamous", _NOT_MET,
                         "case is squamous")
        return _cond("histology", "nonsquamous", _UNKNOWN,
                     f"case histology {case or 'not recorded'}")
    if ind.histology == "squamous":
        if case == "squamous":
            return _cond("histology", "squamous", _MET, "squamous")
        if case in ("adenocarcinoma", "non_squamous"):
            return _cond("histology", "squamous", _NOT_MET,
                         f"case is {case}")
        return _cond("histology", "squamous", _UNKNOWN,
                     f"case histology {case or 'not recorded'}")
    return _cond("histology", "any", _MET, "no histology requirement")


def _eval_driver(ind: Indication, facts: dict[str, Any]) -> dict[str, Any]:
    gene = str(ind.driver or "").lower()
    drivers = _normalized_drivers(facts)
    value = drivers.get(gene)
    status = driver_status(value) if value is not None else "unknown"
    label = ind.driver_class or f"{gene} positive"
    if status == "unknown":
        return _cond("driver", label, _UNKNOWN, f"{gene.upper()} not tested "
                                                f"/ not on record")
    if status == "negative":
        return _cond("driver", label, _NOT_MET, f"{gene.upper()} negative")
    if ind.driver_class is None:
        return _cond("driver", label, _MET, f"{gene.upper()} positive")
    classes = egfr_classes(facts) if gene == "egfr" else frozenset()
    text = str(value)
    if ind.driver_class == "egfr_classical":
        if egfr_classical(facts):
            return _cond("driver", label, _MET,
                         "/".join(sorted(classes)))
        return _cond("driver", label, _NOT_MET,
                     f"EGFR {'/'.join(sorted(classes)) or 'unclassified'} "
                     f"is outside the ex19del/L858R population")
    if ind.driver_class == "egfr_exon20ins":
        return _cond("driver", label,
                     _MET if "exon20ins" in classes else _NOT_MET,
                     "/".join(sorted(classes)) or "unclassified")
    if ind.driver_class == "egfr_uncommon":
        ok = bool(classes & EGFR_UNCOMMON_SENSITIZING) \
            and not (classes & {"exon20ins", "c797s"})
        return _cond("driver", label, _MET if ok else _NOT_MET,
                     "/".join(sorted(classes)) or "unclassified")
    if ind.driver_class == "met_ex14":
        return _cond("driver", label,
                     _MET if _MET_EX14_RE.search(text) else _NOT_MET,
                     text[:60])
    if ind.driver_class == "braf_v600e":
        return _cond("driver", label,
                     _MET if _BRAF_V600_RE.search(text) else _NOT_MET,
                     text[:60])
    return _cond("driver", label, _UNKNOWN,
                 f"declaration error: unknown driver_class "
                 f"{ind.driver_class!r}")


def _eval_no_actionable_driver(ind: Indication,
                               facts: dict[str, Any]) -> dict[str, Any]:
    found = first_line_actionable_drivers(facts)
    if found:
        names = ", ".join(d["gene"] for d in found)
        return _cond("no_actionable_driver", "no first-line actionable driver",
                     _NOT_MET, f"actionable driver on record: {names}")
    from .kg_eligibility import _classify_histology

    case_hist = _classify_histology(str(facts.get("histologic_category") or ""))
    tier_a_unknown = [g.upper() for g in ("egfr", "alk")
                      if driver_status(_normalized_drivers(facts).get(g))
                      == "unknown"]
    if tier_a_unknown and case_hist != "squamous":
        return _cond("no_actionable_driver",
                     "no first-line actionable driver", _UNKNOWN,
                     f"Tier-A testing incomplete: "
                     f"{'/'.join(tier_a_unknown)} unknown in non-squamous "
                     f"disease")
    return _cond("no_actionable_driver", "no first-line actionable driver",
                 _MET,
                 "no actionable driver on record"
                 + ("" if not tier_a_unknown else
                    " (squamous: Tier-A testing optional per guidelines)"))


def _eval_scalars(ind: Indication, facts: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    pd_l1 = facts.get("pd_l1") or {}
    if ind.pd_l1_tps_ge is not None:
        tps = pd_l1.get("tps")
        if isinstance(tps, (int, float)):
            out.append(_cond("pd_l1_tps", f"TPS ≥ {ind.pd_l1_tps_ge:g}%",
                             _MET if tps >= ind.pd_l1_tps_ge else _NOT_MET,
                             f"TPS {tps:g}%"))
        else:
            out.append(_cond("pd_l1_tps", f"TPS ≥ {ind.pd_l1_tps_ge:g}%",
                             _UNKNOWN, "PD-L1 TPS not on record"))
    if ind.pd_l1_tc_ge is not None:
        tc = pd_l1.get("tc", pd_l1.get("tps"))
        if isinstance(tc, (int, float)):
            out.append(_cond("pd_l1_tc", f"TC ≥ {ind.pd_l1_tc_ge:g}%",
                             _MET if tc >= ind.pd_l1_tc_ge else _NOT_MET,
                             f"TC {tc:g}%"))
        else:
            out.append(_cond("pd_l1_tc", f"TC ≥ {ind.pd_l1_tc_ge:g}%",
                             _UNKNOWN, "PD-L1 TC not on record"))
    if ind.resectability:
        resect = str(facts.get("resectability_category")
                     or facts.get("resectability") or "").upper()
        if resect in ("RESECTABLE", "UNRESECTABLE"):
            out.append(_cond("resectability", ind.resectability,
                             _MET if resect == ind.resectability
                             else _NOT_MET, resect.lower()))
        else:
            out.append(_cond("resectability", ind.resectability, _UNKNOWN,
                             "resectability not on record"))
    if ind.requires_inoperable:
        operable = facts.get("operable")
        if operable is False:
            out.append(_cond("operability", "medically inoperable / declines "
                                            "surgery", _MET, "operable=False"))
        elif operable is True:
            out.append(_cond("operability", "medically inoperable / declines "
                                            "surgery", _NOT_MET,
                             "case is operable — surgery is the standard"))
        else:
            out.append(_cond("operability", "medically inoperable / declines "
                                            "surgery", _UNKNOWN,
                             "operability not assessed"))
    if ind.requires_oligometastatic:
        extent = str(facts.get("disease_extent") or "").upper()
        if extent == "OLIGOMETASTATIC":
            out.append(_cond("disease_extent", "oligometastatic (≤3 sites)",
                             _MET, "oligometastatic"))
        elif extent:
            out.append(_cond("disease_extent", "oligometastatic (≤3 sites)",
                             _NOT_MET, extent.lower()))
        else:
            out.append(_cond("disease_extent", "oligometastatic (≤3 sites)",
                             _UNKNOWN, "disease extent not on record"))
    if ind.requires_prior_systemic:
        prior = facts.get("prior_systemic_therapy")
        if prior:
            out.append(_cond("prior_therapy", "previously treated", _MET,
                             str(prior)[:60]))
        elif prior is False:
            out.append(_cond("prior_therapy", "previously treated", _NOT_MET,
                             "treatment-naïve — this is a later-line regimen"))
        else:
            out.append(_cond("prior_therapy", "previously treated", _UNKNOWN,
                             "treatment history not on record"))
    return out


def evaluate_indication(
    regimen_id: str,
    stage_group: str | None,
    facts: dict[str, Any],
) -> dict[str, Any]:
    """Evaluate one regimen's declared population against one case.

    Returns ``{"regimen_id", "declared", "verdict", "conditions",
    "unknown_conditions", "edition_migration"}``; ``declared`` is False for
    a regimen with no declaration (itself a finding — every regimen in the
    library is supposed to carry one).
    """
    ind = INDICATIONS.get(str(regimen_id))
    if ind is None:
        return {"regimen_id": regimen_id, "declared": False,
                "verdict": UNKNOWN, "conditions": [],
                "unknown_conditions": ["indication declaration missing"],
                "edition_migration": False}
    conditions: list[dict[str, Any]] = []
    stage_cond, migrated = _eval_stage(ind, stage_group, facts)
    conditions.append(stage_cond)
    if ind.histology != "any":
        conditions.append(_eval_histology(ind, facts))
    if ind.driver:
        conditions.append(_eval_driver(ind, facts))
    if ind.requires_no_actionable_driver:
        conditions.append(_eval_no_actionable_driver(ind, facts))
    conditions.extend(_eval_scalars(ind, facts))

    if any(c["verdict"] == _NOT_MET for c in conditions):
        verdict = INELIGIBLE
    elif any(c["verdict"] == _UNKNOWN for c in conditions):
        verdict = UNKNOWN
    else:
        verdict = ELIGIBLE
    return {
        "regimen_id": regimen_id,
        "declared": True,
        "verdict": verdict,
        "conditions": conditions,
        "unknown_conditions": [
            f"{c['condition']}: {c['why']}" for c in conditions
            if c["verdict"] == _UNKNOWN
        ],
        "failed_conditions": [
            f"{c['condition']}: {c['why']} (requires {c['requirement']})"
            for c in conditions if c["verdict"] == _NOT_MET
        ],
        "edition_migration": migrated,
        "note": ind.note,
    }


# ----------------------------------------------------------- declarations

def _s(*groups: str) -> frozenset[str]:
    return frozenset(groups)


_EARLY = _s("IB", "IIA", "IIB", "IIIA")
_PERIOP = _s("IIA", "IIB", "IIIA", "IIIB")
_III = _s("IIIA", "IIIB", "IIIC")
_II_III = _s("IIA", "IIB", "IIIA", "IIIB", "IIIC")
_IV = _s("IVA", "IVB")

INDICATIONS: dict[str, Indication] = {i.regimen_id: i for i in (
    # ---------------------------------------------------- adjuvant targeted
    Indication("osimertinib_adjuvant", _EARLY, histology="nonsquamous",
               driver="egfr", driver_class="egfr_classical",
               note="ADAURA: resected IB–IIIA, EGFR ex19del/L858R"),
    Indication("alectinib_adjuvant", _EARLY, driver="alk",
               note="ALINA: resected IB(≥4cm)–IIIA, ALK+"),
    # ---------------------------------------------------- periop / adjuvant IO
    Indication("nivo_chemo_neoadjuvant", _EARLY,
               requires_no_actionable_driver=True,
               resectability="RESECTABLE",
               note="CheckMate 816: resectable, EGFR/ALK excluded"),
    Indication("pembro_perioperative", _PERIOP,
               requires_no_actionable_driver=True,
               resectability="RESECTABLE",
               note="KEYNOTE-671: resectable II–IIIB(N2)"),
    Indication("durva_perioperative", _PERIOP,
               requires_no_actionable_driver=True,
               resectability="RESECTABLE", note="AEGEAN"),
    Indication("nivo_perioperative", _PERIOP,
               requires_no_actionable_driver=True,
               resectability="RESECTABLE", note="CheckMate 77T"),
    Indication("adjuvant_platinum_doublet", _s("IB", "IIA", "IIB", "IIIA",
                                               "IIIB"),
               note="LACE: resected node-positive / high-risk"),
    Indication("atezolizumab_adjuvant", _s("IIA", "IIB", "IIIA"),
               requires_no_actionable_driver=True, pd_l1_tc_ge=1,
               note="IMpower010: adjuvant atezolizumab after chemo, TC≥1%"),
    Indication("pembro_adjuvant", _EARLY,
               requires_no_actionable_driver=True,
               note="KEYNOTE-091 (PEARLS)"),
    # ------------------------------------------------------ definitive local
    Indication("ccrt_60gy", _II_III,
               note="Definitive concurrent chemoradiation, locally advanced"),
    Indication("durva_consolidation", _III,
               requires_no_actionable_driver=True,
               note="PACIFIC: after cCRT without progression; driver-positive "
                    "disease has no established benefit"),
    Indication("osimertinib_consolidation", _III, histology="nonsquamous",
               driver="egfr", driver_class="egfr_classical",
               resectability="UNRESECTABLE",
               note="LAURA: unresectable III, EGFR ex19del/L858R, post-cCRT"),
    Indication("sbrt_definitive", _s("IA1", "IA2", "IA3", "IB"),
               requires_inoperable=True,
               note="Stage I, medically inoperable / declines surgery"),
    Indication("sbrt_oligomet_lct", _s("IVA"),
               requires_oligometastatic=True,
               note="Gomez LCT: ≤3 sites controlled on systemic therapy"),
    # -------------------------------------------------- stage IV, driver+
    Indication("osimertinib_first_line", _IV, driver="egfr",
               driver_class="egfr_classical", note="FLAURA"),
    Indication("osimertinib_chemo_first_line", _IV, histology="nonsquamous",
               driver="egfr", driver_class="egfr_classical", note="FLAURA2"),
    Indication("amivantamab_lazertinib", _IV, driver="egfr",
               driver_class="egfr_classical", note="MARIPOSA"),
    Indication("amivantamab_chemo_first_line", _IV, histology="nonsquamous",
               driver="egfr", driver_class="egfr_exon20ins",
               note="PAPILLON: EGFR exon 20 insertion"),
    Indication("afatinib_uncommon_first_line", _IV, driver="egfr",
               driver_class="egfr_uncommon",
               note="LUX-Lung pooled: G719X/L861Q/S768I"),
    Indication("lorlatinib_first_line", _IV, driver="alk", note="CROWN"),
    Indication("repotrectinib_first_line", _IV, driver="ros1",
               note="TRIDENT-1: ROS1 fusion"),
    Indication("selpercatinib_first_line", _IV, driver="ret",
               note="LIBRETTO-431: RET fusion"),
    Indication("capmatinib_first_line", _IV, driver="met",
               driver_class="met_ex14",
               note="GEOMETRY: MET exon 14 skipping"),
    Indication("dabrafenib_trametinib_first_line", _IV, driver="braf",
               driver_class="braf_v600e", note="BRF113928: BRAF V600E"),
    Indication("larotrectinib_first_line", _IV, driver="ntrk",
               note="NAVIGATE: NTRK fusion"),
    # ------------------------------------------------ stage IV, driver-negative
    Indication("pembro_monotherapy", _IV, pd_l1_tps_ge=50,
               requires_no_actionable_driver=True,
               note="KEYNOTE-024: TPS≥50%, driver-negative"),
    Indication("pembro_pemetrexed_platinum", _IV, histology="nonsquamous",
               requires_no_actionable_driver=True, note="KEYNOTE-189"),
    Indication("pembro_carbo_taxane", _IV, histology="squamous",
               requires_no_actionable_driver=True, note="KEYNOTE-407"),
    Indication("nivo_ipi_chemo", _IV, requires_no_actionable_driver=True,
               note="CheckMate 9LA"),
    # ------------------------------------------------------------ later line
    Indication("tdxd_subsequent_line", _IV, driver="erbb2",
               requires_prior_systemic=True,
               note="DESTINY-Lung02: previously treated HER2-mutant"),
)}


def undeclared_regimens() -> list[str]:
    """Library regimens without an indication declaration — should be []."""
    from . import regimens as regimen_lib

    return sorted(r.regimen_id for r in regimen_lib.REGIMENS
                  if r.regimen_id not in INDICATIONS)
