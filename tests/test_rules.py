"""The deterministic safety rule engine — every rule, both directions."""

from nsclc_agent.safety.rules import check_plan


def _violation_ids(staging, facts, plan):
    return {v.rule_id for v in check_plan(staging, facts, plan)}


STAGING_IIIB_N3 = {"stage_group": "IIIC", "n_category": "N3"}
STAGING_IIIB = {"stage_group": "IIIB", "n_category": "N2b"}
STAGING_IIIA = {"stage_group": "IIIA", "n_category": "N2a"}
STAGING_IVB = {"stage_group": "IVB", "n_category": "N2b"}
EGFR_POS = {"driver_mutations": {"egfr": "L858R", "alk": "negative"},
            "histologic_category": "adenocarcinoma"}
DRIVER_NEG = {"driver_mutations": {"egfr": "negative", "alk": "negative"},
              "histologic_category": "adenocarcinoma"}


def test_n3_no_surgery_fires():
    plan = {"summary": "Upfront lobectomy then adjuvant therapy",
            "regimen_ids": [], "options": []}
    assert "N3_NO_SURGERY" in _violation_ids(STAGING_IIIB_N3, DRIVER_NEG, plan)


def test_n3_ccrt_clean():
    plan = {"regimen_ids": ["ccrt_60gy", "durva_consolidation"], "options": []}
    assert "N3_NO_SURGERY" not in _violation_ids(STAGING_IIIB_N3, DRIVER_NEG, plan)


def test_driver_excludes_periop_io():
    plan = {"regimen_ids": ["pembro_perioperative"], "options": []}
    ids = _violation_ids(STAGING_IIIA, EGFR_POS, plan)
    assert "DRIVER_EXCLUDES_PERIOP_IO" in ids


def test_driver_excludes_periop_io_by_trial_ref():
    plan = {"regimen_ids": [], "trial_refs": ["CheckMate 816"], "options": []}
    assert "DRIVER_EXCLUDES_PERIOP_IO" in _violation_ids(STAGING_IIIA, EGFR_POS, plan)


def test_egfr_iii_durvalumab_blocked():
    plan = {"regimen_ids": ["ccrt_60gy", "durva_consolidation"], "options": []}
    assert "EGFR_III_CONSOLIDATION" in _violation_ids(STAGING_IIIB, EGFR_POS, plan)


def test_egfr_iii_osimertinib_clean():
    plan = {"regimen_ids": ["ccrt_60gy", "osimertinib_consolidation"], "options": []}
    ids = _violation_ids(STAGING_IIIB, EGFR_POS, plan)
    assert "EGFR_III_CONSOLIDATION" not in ids


def test_concurrent_durvalumab_blocked():
    plan = {"summary": "cCRT with concurrent durvalumab from day 1",
            "regimen_ids": [], "options": []}
    assert "NO_CONCURRENT_DURVALUMAB" in _violation_ids(STAGING_IIIB, DRIVER_NEG, plan)


def test_rt_dose_escalation_blocked():
    plan = {"summary": "Escalate radiotherapy to 74 Gy for bulky disease",
            "regimen_ids": [], "options": []}
    assert "NO_RT_DOSE_ESCALATION" in _violation_ids(STAGING_IIIB, DRIVER_NEG, plan)


def test_rt_60gy_reference_clean():
    plan = {"summary": "Standard 60 Gy in 30 fractions", "regimen_ids": [], "options": []}
    assert "NO_RT_DOSE_ESCALATION" not in _violation_ids(STAGING_IIIB, DRIVER_NEG, plan)


def test_trial_stage_boundary_blocks_undeclared():
    plan = {"regimen_ids": ["nivo_chemo_neoadjuvant"], "options": []}  # CM816: IB–IIIA
    assert "TRIAL_STAGE_BOUNDARY" in _violation_ids(STAGING_IIIB, DRIVER_NEG, plan)


def test_trial_stage_boundary_declared_extrapolation_warns():
    plan = {
        "regimen_ids": ["osimertinib_adjuvant"],  # ADAURA: IB–IIIA
        "extrapolations": [{"trial_id": "ADAURA",
                            "justification": "MDT-documented extrapolation"}],
        "options": [],
    }
    violations = check_plan(STAGING_IIIB, EGFR_POS, plan)
    by_id = {v.rule_id: v.severity for v in violations}
    assert by_id.get("TRIAL_STAGE_EXTRAPOLATION") == "warn"
    assert "TRIAL_STAGE_BOUNDARY" not in by_id


def test_stage0_systemic_blocked():
    plan = {"regimen_ids": ["adjuvant_platinum_doublet"], "options": []}
    ids = _violation_ids({"stage_group": "0", "n_category": "N0"}, DRIVER_NEG, plan)
    assert "STAGE0_NO_SYSTEMIC" in ids


def test_driver_first_line_blocked():
    plan = {"regimen_ids": ["pembro_pemetrexed_platinum"], "options": []}
    assert "DRIVER_FIRST_LINE" in _violation_ids(STAGING_IVB, EGFR_POS, plan)


def test_driver_first_line_targeted_clean():
    plan = {"regimen_ids": ["osimertinib_first_line"], "options": []}
    assert "DRIVER_FIRST_LINE" not in _violation_ids(STAGING_IVB, EGFR_POS, plan)


def test_ici_comorbidity_warns():
    facts = dict(DRIVER_NEG)
    facts["comorbidities"] = {"ild": True}
    plan = {"regimen_ids": ["pembro_pemetrexed_platinum"], "options": []}
    violations = check_plan(STAGING_IVB, facts, plan)
    hit = next(v for v in violations if v.rule_id == "ICI_COMORBIDITY_CAUTION")
    assert hit.severity == "warn"


def test_ps_gate_warns():
    facts = dict(DRIVER_NEG)
    facts["ecog_ps"] = 3
    plan = {"regimen_ids": ["ccrt_60gy", "durva_consolidation"], "options": []}
    assert "PS_GATE" in {v.rule_id for v in check_plan(STAGING_IIIB, facts, plan)}


def test_biomarker_gap_blocks_systemic_commitment():
    facts = {"driver_mutations": {"egfr": "not_tested", "alk": "not_tested"},
             "histologic_category": "adenocarcinoma"}
    plan = {"regimen_ids": ["pembro_pemetrexed_platinum"], "options": []}
    assert "BIOMARKER_GAP" in _violation_ids(STAGING_IVB, facts, plan)


def test_biomarker_gap_squamous_carveout():
    facts = {"driver_mutations": {}, "histologic_category": "squamous"}
    plan = {"regimen_ids": ["pembro_carbo_taxane"], "options": []}
    assert "BIOMARKER_GAP" not in _violation_ids(STAGING_IVB, facts, plan)


def test_dose_scan_catches_model_numerics():
    plan = {"summary": "Give pembrolizumab 200 mg every 3 weeks",
            "regimen_ids": [], "options": []}
    assert "DOSE_IN_MODEL_OUTPUT" in _violation_ids(STAGING_IVB, DRIVER_NEG, plan)


def test_dose_scan_ignores_regimen_identifiers():
    """`ccrt_60gy` is a library key, not an authored numeric."""
    plan = {"regimen_ids": ["ccrt_60gy"], "options": [
        {"name": "cCRT", "regimen_ids": ["ccrt_60gy"], "rationale": "standard"}]}
    assert "DOSE_IN_MODEL_OUTPUT" not in _violation_ids(STAGING_IIIB, DRIVER_NEG, plan)


def test_dose_plan_channel_exempt():
    plan = {"regimen_ids": [], "options": [],
            "dose_plan": {"regimens": [{"drug": "pembrolizumab", "dose": "200 mg"}]}}
    assert "DOSE_IN_MODEL_OUTPUT" not in _violation_ids(STAGING_IVB, DRIVER_NEG, plan)


def test_blockers_sort_first():
    plan = {"summary": "Upfront lobectomy at 74 Gy",  # nonsense, two blockers
            "regimen_ids": [], "options": []}
    facts = dict(DRIVER_NEG)
    facts["ecog_ps"] = 3
    violations = check_plan(STAGING_IIIB_N3, facts, plan)
    severities = [v.severity for v in violations]
    assert severities == sorted(severities, key=lambda s: 0 if s == "block" else 1)
