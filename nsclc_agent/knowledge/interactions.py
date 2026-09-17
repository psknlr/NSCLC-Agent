"""Built-in oncology drug-interaction rule pack (compact, reviewable).

Not a substitute for a full interaction database — the point is that the most
consequential, well-established NSCLC interaction classes are checked
deterministically and versionably, the same way YaoBi-Harness ships its
orthopaedic pack. A licensed knowledge store, when configured, supersedes this.
"""

from __future__ import annotations

from dataclasses import dataclass

PACK_VERSION = "2026-08"

#: Severity ladder: contraindicated > major > moderate > monitor
SEVERITIES = ("contraindicated", "major", "moderate", "monitor")


@dataclass(frozen=True)
class InteractionRule:
    rule_id: str
    #: Substrings matched (lowercase) against one side's drug names.
    left: tuple[str, ...]
    right: tuple[str, ...]
    severity: str
    mechanism: str
    action: str


RULES: tuple[InteractionRule, ...] = (
    InteractionRule(
        "lorlatinib_cyp3a4_inducer",
        ("lorlatinib", "洛拉替尼"),
        ("rifampin", "rifampicin", "carbamazepine", "phenytoin", "st john", "利福平", "卡马西平", "苯妥英"),
        "contraindicated",
        "Strong CYP3A4 induction — severe hepatotoxicity observed with rifampin co-administration",
        "Do not combine; switch or washout the inducer before starting lorlatinib",
    ),
    InteractionRule(
        "osimertinib_cyp3a4_inducer",
        ("osimertinib", "奥希替尼"),
        ("rifampin", "rifampicin", "carbamazepine", "phenytoin", "st john", "利福平", "卡马西平", "苯妥英"),
        "major",
        "Strong CYP3A4 induction markedly reduces osimertinib exposure",
        "Avoid; if unavoidable, increase osimertinib per label and monitor response",
    ),
    InteractionRule(
        "osimertinib_qt",
        ("osimertinib", "奥希替尼"),
        ("citalopram", "escitalopram", "amiodarone", "sotalol", "moxifloxacin", "ondansetron",
         "西酞普兰", "胺碘酮", "莫西沙星", "昂丹司琼"),
        "moderate",
        "Additive QTc prolongation",
        "Baseline and periodic ECG + electrolytes; prefer non-QT alternatives",
    ),
    InteractionRule(
        "pemetrexed_nsaid",
        ("pemetrexed", "培美曲塞"),
        ("ibuprofen", "naproxen", "diclofenac", "indomethacin", "布洛芬", "萘普生", "双氯芬酸"),
        "major",
        "NSAIDs reduce renal clearance of pemetrexed — myelosuppression risk, worst with renal impairment",
        "Hold short-half-life NSAIDs 2 days before to 2 days after pemetrexed (5 days for long-acting)",
    ),
    InteractionRule(
        "cisplatin_nephrotoxin",
        ("cisplatin", "顺铂"),
        ("gentamicin", "amikacin", "tobramycin", "vancomycin", "amphotericin", "庆大霉素", "万古霉素"),
        "major",
        "Additive nephrotoxicity",
        "Avoid concurrent nephrotoxins; if unavoidable, aggressive hydration and renal monitoring",
    ),
    InteractionRule(
        "ici_systemic_steroid",
        ("pembrolizumab", "nivolumab", "durvalumab", "atezolizumab", "ipilimumab",
         "帕博利珠", "纳武利尤", "度伐利尤", "阿替利珠", "伊匹木"),
        ("prednisone", "prednisolone", "methylprednisolone", "dexamethasone",
         "泼尼松", "甲泼尼龙", "地塞米松"),
        "monitor",
        "Baseline systemic corticosteroids (>10 mg prednisone-equivalent) associated with reduced ICI efficacy",
        "Review the steroid indication; taper below 10 mg prednisone-equivalent where feasible before ICI start",
    ),
    InteractionRule(
        "ici_live_vaccine",
        ("pembrolizumab", "nivolumab", "durvalumab", "atezolizumab", "ipilimumab"),
        ("live vaccine", "mmr", "varicella", "yellow fever", "减毒活疫苗"),
        "major",
        "Live vaccines on checkpoint blockade carry disseminated-infection and irAE risk",
        "Avoid live vaccines during and shortly after ICI therapy; use inactivated alternatives",
    ),
    InteractionRule(
        "warfarin_tki",
        ("warfarin", "华法林"),
        ("osimertinib", "alectinib", "lorlatinib", "gefitinib", "erlotinib",
         "奥希替尼", "阿来替尼", "洛拉替尼", "吉非替尼", "厄洛替尼"),
        "monitor",
        "TKI-warfarin pharmacokinetic interplay; INR drift reported with several EGFR/ALK TKIs",
        "Check INR weekly for the first month after starting/stopping the TKI; consider DOAC where appropriate",
    ),
    InteractionRule(
        "carboplatin_phenytoin",
        ("carboplatin", "cisplatin", "卡铂", "顺铂"),
        ("phenytoin", "苯妥英"),
        "moderate",
        "Platinum agents reduce phenytoin absorption/levels — seizure risk",
        "Monitor phenytoin levels each cycle; consider levetiracetam",
    ),
    InteractionRule(
        "paclitaxel_cyp2c8_inhibitor",
        ("paclitaxel", "紫杉醇"),
        ("gemfibrozil", "clopidogrel", "吉非罗齐", "氯吡格雷"),
        "moderate",
        "CYP2C8 inhibition raises paclitaxel exposure — neuropathy/neutropenia risk",
        "Prefer alternatives; monitor CBC and neuropathy closely if combined",
    ),
)


def check(medications: list[str]) -> list[dict]:
    """Screen a medication list; returns hits sorted most-severe first."""
    meds = [str(m).strip().lower() for m in medications if str(m).strip()]
    hits: list[dict] = []
    for rule in RULES:
        left_hit = [m for m in meds if any(k in m for k in rule.left)]
        right_hit = [m for m in meds if any(k in m for k in rule.right)]
        # A drug matching both sides of the same rule is one drug, not a pair.
        if left_hit and right_hit and set(left_hit) != set(right_hit):
            hits.append({
                "rule_id": rule.rule_id,
                "drugs": sorted(set(left_hit) | set(right_hit)),
                "severity": rule.severity,
                "mechanism": rule.mechanism,
                "action": rule.action,
            })
    hits.sort(key=lambda h: SEVERITIES.index(h["severity"]))
    return hits
