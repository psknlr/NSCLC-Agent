"""Perception validation + prompt module loader + schemas + trials/regimens."""

import json
import re

import pytest

from nsclc_agent import schemas
from nsclc_agent.knowledge import regimens as regimen_lib
from nsclc_agent.knowledge import trials as trial_lib
from nsclc_agent.llm.base import LLMResponse
from nsclc_agent.perception import (
    ImagingError, ImagingFindings, ImagingReader, fold_into_case,
    validate_descriptor,
)
from nsclc_agent.perception.imaging import mock_findings_payload
from nsclc_agent.prompts import MODULES, find_sections, list_modules, load_module
from nsclc_agent.prompts.cores import MIN_OUTPUT_TOKENS, STAGE_CORES


# ---------------------------------------------------------------- perception

def test_validate_descriptor_normalizes():
    assert validate_descriptor("t", "t2a") == ("T2a", None)
    assert validate_descriptor("n", "N2B") == ("N2b", None)
    assert validate_descriptor("m", None) == (None, None)
    assert validate_descriptor("t", "null") == (None, None)


def test_validate_descriptor_rejects_engine_refusals():
    value, rejection = validate_descriptor("n", "N2")
    assert value is None
    assert "IMAGING_DESCRIPTOR_REJECTED[N]" in rejection
    value, rejection = validate_descriptor("t", "T2")
    assert value is None and "T2a" in rejection


def test_fold_cross_check_normalizes_both_sides():
    """'t2a' vs proposed 'T2a' is agreement — the v0.1 false discordance."""
    findings = ImagingFindings(candidate_t="T2a")
    _, flags = fold_into_case("t2a", "N0", "M0", findings)
    assert not any("DISCORDANCE" in f for f in flags)


def test_fold_flags_real_discordance():
    findings = ImagingFindings(candidate_t="T4")
    _, flags = fold_into_case("T2a", "N0", "M0", findings)
    assert any("IMAGING_DISCORDANCE[T]" in f for f in flags)


def test_fold_seeds_missing_descriptors():
    findings = ImagingFindings(candidate_n="N2a", candidate_m="M0")
    seeded, flags = fold_into_case("T2a", None, None, findings)
    assert seeded == {"n": "N2a", "m": "M0"}
    assert any("RADIOGRAPHIC_TNM_PROPOSED" in f for f in flags)


def test_reader_refuses_text_model():
    class TextOnly:
        available = True
        supports_vision = False
        name = "text"

    with pytest.raises(ImagingError, match="not vision-capable"):
        ImagingReader(TextOnly())


def test_reader_parses_and_validates_proposals():
    class VisionMock:
        available = True
        supports_vision = True
        name = "vis"
        model = "vis"

        def chat(self, messages, **kwargs):
            return LLMResponse(text=json.dumps({
                "modality": "PET-CT", "candidate_t": "T2a",
                "candidate_n": "N2",  # forbidden bare N2 → must be rejected
                "candidate_m": "M0", "nodal_stations": ["4R", "7"],
                "confidence": "moderate", "uncertainties": [],
            }))

    reader = ImagingReader(VisionMock())
    findings = reader.read(["data:image/png;base64,aGk="])
    assert findings.candidate_t == "T2a"
    assert findings.candidate_n is None
    assert any("IMAGING_DESCRIPTOR_REJECTED[N]" in r
               for r in findings.rejected_descriptors)


def test_mock_findings_payload_is_honest():
    payload = json.loads(mock_findings_payload())
    assert payload["candidate_t"] is None
    assert payload["uncertainties"]


# ------------------------------------------------------------------- prompts

def test_all_modules_load_with_provenance():
    for module in list_modules():
        assert module.full_text
        assert len(module.sha256) == 64
        assert module.sections, module.key
        assert module.min_output_tokens >= 1500


def test_every_module_has_a_core():
    assert set(STAGE_CORES) == set(MODULES)
    assert set(MIN_OUTPUT_TOKENS) == set(MODULES)


def test_find_sections_by_stage_and_query():
    sections, key = find_sections("IIIB", "durvalumab consolidation")
    assert key == "stage3b" and sections
    sections, key = find_sections("Stage IIIA", "")
    assert key == "stage3a" and sections
    sections, key = find_sections("banana", "")
    assert key is None and sections == []


def test_cores_are_dose_free():
    from nsclc_agent.safety.rules import DOSE_RE

    for key, core in STAGE_CORES.items():
        scrubbed = core
        for rid in regimen_lib.REGIMENS_BY_ID:
            scrubbed = scrubbed.replace(rid, "")
        # The RT dose standard (60 Gy) is protocol fact, allowed in cores;
        # drug doses are not.
        scrubbed = re.sub(r"\d+\s*Gy(?:/\d+\s*fx)?", "", scrubbed)
        assert not re.search(r"\d+\s*(?:mg|毫克)", scrubbed), key
        assert DOSE_RE is not None


# ------------------------------------------------------------------- schemas

def test_schema_validation_catches_shape_errors():
    ok, problems = schemas.validate("TreatmentPlan", {"intent": "curative"})
    assert not ok and any("summary" in p for p in problems)
    ok, _ = schemas.validate("TreatmentPlan", {
        "intent": "curative", "summary": "s", "options": [], "regimen_ids": []})
    assert ok


def test_imaging_findings_cannot_claim_confirmation():
    ok, problems = schemas.validate("ImagingFindings",
                                    {"requires_confirmation": False})
    assert not ok and any("requires_confirmation" in p for p in problems)


def test_panel_urgency_vocabulary_enforced():
    ok, problems = schemas.validate("PanelOpinion", {
        "urgency": "catastrophic", "key_findings": [], "concerns": [],
        "recommend_next": []})
    assert not ok


def test_unknown_schema_is_a_failure():
    ok, problems = schemas.validate("NoSuchSchema", {})
    assert not ok


# ------------------------------------------------------------ knowledge data

def test_trial_registry_integrity():
    for trial in trial_lib.TRIALS:
        for rid in trial.regimen_ids:
            assert rid in regimen_lib.REGIMENS_BY_ID, (trial.trial_id, rid)
    for regimen in regimen_lib.REGIMENS:
        for tid in regimen.trial_ids:
            assert tid in trial_lib.TRIALS_BY_ID, (regimen.regimen_id, tid)


def test_periop_io_trial_set():
    assert {"CHECKMATE816", "KEYNOTE671", "AEGEAN", "CHECKMATE77T",
            "IMPOWER010", "KEYNOTE091"} <= trial_lib.PERIOP_ADJUVANT_IO_TRIALS
    assert "PACIFIC" not in trial_lib.PERIOP_ADJUVANT_IO_TRIALS


def test_stage_boundaries_encode_the_teaching_cases():
    assert "IIIB" not in trial_lib.TRIALS_BY_ID["CHECKMATE816"].stage_groups
    assert "IIIB" in trial_lib.TRIALS_BY_ID["KEYNOTE671"].stage_groups
    assert "IIIB" not in trial_lib.TRIALS_BY_ID["ADAURA"].stage_groups
    assert trial_lib.TRIALS_BY_ID["PACIFIC2"].stage_groups == frozenset()


def test_regimen_summary_dose_free_but_detail_dosed():
    for regimen in regimen_lib.REGIMENS:
        summary_blob = json.dumps(regimen.summary(), ensure_ascii=False)
        summary_blob = summary_blob.replace(regimen.regimen_id, "")
        assert not re.search(r"\d+\s*mg", summary_blob), regimen.regimen_id
    detail = regimen_lib.get("pembro_monotherapy").detail()
    assert "200 mg" in json.dumps(detail)
