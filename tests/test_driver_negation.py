"""Negation written BEFORE the genes it covers (v1.4.1 red-team finding).

Read from the gene name forward, "No ALK rearrangement" lost its "No" and
read ALK-POSITIVE; the governed chat then released lorlatinib,
dabrafenib-trametinib or capmatinib for driver-negative patients."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nsclc_agent.conversation import ConsultationSession, extract_facts_deterministic  # noqa: E402
from nsclc_agent.knowledge.biomarkers import driver_status  # noqa: E402


def _status(text):
    drivers = extract_facts_deterministic(text).get("driver_mutations") or {}
    return {gene: driver_status(value) for gene, value in drivers.items()}


@pytest.mark.parametrize("text,negative", [
    ("No ALK rearrangement.", ["alk"]),
    ("Negative for BRAF V600E.", ["braf"]),
    ("Absence of MET exon 14 skipping.", ["met"]),
    ("无RET融合。", ["ret"]),
    ("未检出 EGFR、ALK、ROS1、BRAF V600E、MET 14 跳跃突变。", ["egfr", "alk", "ros1", "braf", "met"]),
    ("NGS negative for RET, MET exon 14, BRAF V600E, KRAS G12C, HER2 and NTRK.",
     ["ret", "met", "braf", "kras", "erbb2", "ntrk"]),
    ("Not detected: EGFR, ALK, ROS1.", ["egfr", "alk", "ros1"]),
    ("阴性：EGFR、ALK、ROS1。", ["egfr", "alk", "ros1"]),
    ("There is no EGFR mutation and no ALK fusion.", ["egfr", "alk"]),
])
def test_prefix_negation_reads_negative(text, negative):
    status = _status(text)
    for gene in negative:
        assert status.get(gene) == "negative", (gene, status)
    assert "positive" not in status.values(), status


@pytest.mark.parametrize("text,positive,negative", [
    ("No ALK rearrangement. EGFR L858R detected.", ["egfr"], ["alk"]),
    ("Negative for EGFR and ALK; KRAS G12C positive.", ["kras"], ["egfr", "alk"]),
    ("未检出 EGFR、ALK 突变，KRAS G12C 阳性。", ["kras"], ["egfr", "alk"]),
    ("EGFR L858R detected; T790M not detected.", ["egfr"], []),
    ("No EGFR T790M. EGFR exon 19 deletion detected.", ["egfr"], []),
])
def test_positive_statements_survive_nearby_negation(text, positive, negative):
    status = _status(text)
    for gene in positive:
        assert status.get(gene) == "positive", (gene, status)
    for gene in negative:
        assert status.get(gene) == "negative", (gene, status)


@pytest.mark.parametrize("text,gene", [
    ("EGFR T790M not detected.", "egfr"),        # resistance variant only
    ("No MET amplification.", "met"),             # copy number, not ex14
    ("No EGFR testing was done.", "egfr"),        # testing, not a result
    ("无足够组织检测EGFR。", "egfr"),              # no tissue, not a result
    ("No liver metastases.", "met"),              # 'met' inside a word
    ("No EGFR TKI before.", "egfr"),              # treatment, not a result
])
def test_statements_that_say_nothing_about_the_driver_record_nothing(text, gene):
    assert gene not in _status(text)


def test_governed_chat_no_longer_releases_targeted_therapy_for_a_negated_driver():
    session = ConsultationSession(role="oncologist")
    result = session.turn(
        "62-year-old man, lung adenocarcinoma cT3N2bM1c2 with liver and bone metastases, "
        "ECOG 1. Brain MRI negative. No hemoptysis, no headache, no limb weakness. PD-L1 TPS "
        "80%. No ALK rearrangement. EGFR, ROS1, BRAF, MET, RET, KRAS, HER2, NTRK negative.")
    regimens = (result.state.outputs.get("treatment_plan") or {}).get("regimen_ids") or []
    assert result.state.release_status == "treatment_recommendation"
    assert not any("lorlatinib" in r or "alectinib" in r for r in regimens), regimens
    assert "pembro_monotherapy" in regimens
