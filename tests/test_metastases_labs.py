"""Metastatic sites beyond the brain, laboratory values, organ damage and
the organ gates that read them (v1.4.0)."""

from __future__ import annotations

import pytest

from nsclc_agent.conversation import (
    ConsultationSession, extract_facts_deterministic as extract, merge_facts,
    sanitize_fact_payload,
)
from nsclc_agent.knowledge import regimens as regimen_lib
from nsclc_agent.knowledge.labs import crcl_estimate, extract_labs, supportive_notes as lab_notes
from nsclc_agent.knowledge.metastases import extract_sites, suggest_m, supportive_notes, tnm_conflict
from nsclc_agent.knowledge.organ_gates import evaluate_gate, regimen_gates
from nsclc_agent.safety import rules
from tests.test_case_kras_later_line import CASE

# ------------------------------------------------------------- sites


def test_the_kras_case_sites_and_m_category():
    sites = extract_sites(CASE)
    # "侵犯纵隔胸膜" is T4 invasion, never a pleural metastasis on its own;
    # the later pleural lesions and the malignant effusion are.
    assert sites == {"bone": "multiple", "liver": "multiple", "adrenal": "single",
                     "pleura": "present", "pleural_effusion": "present"}
    facts = extract(CASE)
    assert facts["metastatic_sites"] == sites
    suggestion = suggest_m(facts)
    assert suggestion["m"] == "M1c2"
    assert tnm_conflict(facts) is None  # recorded M1c is the M1c family


@pytest.mark.parametrize("text,expected", [
    ("右肺腺癌，侵犯纵隔胸膜", {}),
    ("无骨转移，肝脏未见转移", {"bone": "absent", "liver": "absent"}),
    ("骨扫描阴性", {"bone": "absent"}),
    ("可疑骨转移", {}),
    ("肝转移？", {}),
    ("肾上腺结节，性质待定", {}),
    ("单发肝转移", {"liver": "single"}),
    ("新出现2个肝转移灶", {"liver": "multiple"}),
    ("双侧肾上腺转移", {"adrenal": "multiple"}),
    ("左肾上腺及多发骨转移", {"adrenal": "single", "bone": "multiple"}),
    ("肝、骨多发转移", {"liver": "multiple", "bone": "multiple"}),
    ("双肺多发转移结节，腹膜后淋巴结转移",
     {"contralateral_lung": "multiple", "distant_lymph_nodes": "present"}),
    ("左侧胸腔积液", {}),
    ("胸水找到腺癌细胞", {"pleural_effusion": "present"}),
    ("腹膜转移", {"other": "present"}),
    ("solitary adrenal metastasis; no liver metastases", {"adrenal": "single", "liver": "absent"}),
])
def test_site_extraction(text, expected):
    assert extract_sites(text) == expected


@pytest.mark.parametrize("sites,cns,m", [
    ({"pleural_effusion": "present"}, None, "M1a"),
    ({"contralateral_lung": "multiple", "pleura": "present"}, None, "M1a"),
    ({"bone": "single"}, None, "M1b"),
    ({"liver": "multiple"}, None, "M1c1"),
    ({"bone": "single"}, {"status": "present", "burden": "limited"}, "M1c2"),
    ({"bone": "present"}, None, None),  # M1b or M1c1: the count decides
    ({}, {"status": "present", "burden": "limited"}, "M1b"),
])
def test_suggested_m(sites, cns, m):
    facts = {"metastatic_sites": sites}
    if cns:
        facts["cns_metastases"] = cns
    assert suggest_m(facts)["m"] == m


def test_a_contradicted_m_is_named_never_repaired():
    facts = {"tnm": {"t": "T2a", "n": "N1", "m": "M1a"},
             "metastatic_sites": {"liver": "multiple", "bone": "single"}}
    found = rules.check_plan({"stage_group": "IVA"}, facts, {"regimen_ids": [], "options": []})
    hit = [v for v in found if v.rule_id == "METASTASIS_TNM_INCONSISTENT"]
    assert hit and hit[0].severity == "warn" and "M1c2" in hit[0].message
    assert facts["tnm"]["m"] == "M1a"
    assert tnm_conflict({"tnm": {"m": "M0"}, "metastatic_sites": {"bone": "single"}})
    assert tnm_conflict({"tnm": {"m": "cM1c"}, "metastatic_sites": {"bone": "multiple"}}) is None
    assert tnm_conflict({"tnm": {"m": "M0"}, "metastatic_sites": {"bone": "absent"}}) is None


def test_sites_are_validated():
    cleaned, notes = sanitize_fact_payload({"metastatic_sites": {
        "bone": "多发", "liver": True, "brain": "present", "spleen": "present", "adrenal": "maybe"}})
    assert cleaned["metastatic_sites"] == {"bone": "multiple", "liver": "present"}
    assert any("cns_metastases" in n for n in notes) and any("spleen" in n for n in notes)


def test_supportive_care_from_sites():
    notes = supportive_notes({"metastatic_sites": {"bone": "multiple", "pleural_effusion": "present"}})
    assert any("骨改良药物" in n for n in notes) and any("胸腔导管" in n for n in notes)


# ------------------------------------------------------------- labs

LAB_TEXT = """68岁女性，体重 52 kg，身高 158 cm。血常规：WBC 3.2×10⁹/L，中性粒细胞 1.2×10⁹/L，Hb 98 g/L，PLT 85×10⁹/L。
肝功能：ALT 120 U/L，AST 2.5×ULN，总胆红素 30 µmol/L，白蛋白 32 g/L。血肌酐 110 µmol/L，eGFR 48。
LVEF 45%，QTc 470 ms，FEV1 55%，DLCO 38%。血钙 2.9 mmol/L，血钠 128 mmol/L。
既往高血压、2型糖尿病，乙肝 HBsAg 阳性，无间质性肺病，否认冠心病。2级周围神经病变。"""


def test_lab_extraction():
    facts = extract_labs(LAB_TEXT)
    organ = facts["organ_function"]
    assert organ["hematologic"] == {"wbc": 3.2, "anc": 1.2, "hb": 98.0, "plt": 85.0}
    assert organ["hepatic"] == {"alt_u_l": 120.0, "ast_uln": 2.5, "bilirubin_umol_l": 30.0,
                                "albumin_g_l": 32.0}
    assert organ["renal"] == {"egfr_ml_min": 48.0, "creatinine_umol_l": 110.0}
    assert organ["cardiac"] == {"lvef_pct": 45.0}
    assert organ["pulmonary"] == {"fev1_pct": 55.0, "dlco_pct": 38.0}
    assert organ["electrolytes"] == {"calcium_mmol_l": 2.9, "sodium_mmol_l": 128.0}
    assert facts["qtc_ms"] == 470 and facts["weight_kg"] == 52 and facts["sex"] == "female"
    assert facts["comorbidities"] == {"ild": False, "coronary_artery_disease": False,
                                      "hypertension": True, "diabetes": True,
                                      "peripheral_neuropathy": 2, "hbv": True}


def test_units_are_converted():
    facts = extract_labs("Male, weight 80 kg, creatinine 1.4 mg/dL, Hb 11.2 g/dL, ANC 1500/uL")
    assert facts["organ_function"]["renal"]["creatinine_umol_l"] == pytest.approx(123.8)
    assert facts["organ_function"]["hematologic"] == {"hb": 112.0, "anc": 1.5}
    # weight loss is not a weight; a tumour marker is not a calcium
    assert extract_labs("体重下降 5 kg，CA125 35") == {}
    assert extract_labs("HBsAg阴性，无心衰")["comorbidities"] == {"hbv": False, "heart_failure": False}


def test_out_of_range_values_are_refused_not_clipped():
    cleaned, notes = sanitize_fact_payload({"organ_function": {
        "renal": {"crcl_ml_min": 500}, "hematologic": {"plt": "85"}}, "weight_kg": "60 kg"})
    assert cleaned["organ_function"] == {"hematologic": {"plt": 85.0}}
    assert cleaned["weight_kg"] == 60.0
    assert any("crcl_ml_min" in n for n in notes)


def test_nested_lab_facts_merge_field_by_field():
    record = {"organ_function": {"renal": {"crcl_ml_min": 50}}}
    changed, conflicts = merge_facts(
        record, {"organ_function": {"renal": {"creatinine_umol_l": 100}}}, overwrite=False)
    assert record["organ_function"]["renal"] == {"crcl_ml_min": 50, "creatinine_umol_l": 100}
    assert changed == ["organ_function.renal.creatinine_umol_l"] and conflicts == []


def test_cockcroft_gault_brackets_an_unknown_sex():
    facts = {"age": 68, "weight_kg": 52, "organ_function": {"renal": {"creatinine_umol_l": 110}}}
    estimate = crcl_estimate(facts)
    assert estimate["low"] < estimate["high"]
    assert evaluate_gate("renal_function", facts)["status"] == "fail"  # both readings < 45
    facts["weight_kg"] = 60  # female 41.0, male 48.2 mL/min: straddles 45
    assert evaluate_gate("renal_function", facts)["status"] == "unverified"
    facts["sex"] = "male"
    assert evaluate_gate("renal_function", facts)["status"] == "pass"
    assert crcl_estimate({"age": 68, "organ_function": {"renal": {"creatinine_umol_l": 110}}}) is None


# ------------------------------------------------------------- gates

@pytest.mark.parametrize("gate,facts,status", [
    ("hematologic_baseline", {"organ_function": {"hematologic": {"anc": 1.2, "plt": 85}}}, "unverified"),
    ("hematologic_baseline", {"organ_function": {"hematologic": {"anc": 3.0, "plt": 200}}}, "pass"),
    ("hematologic_baseline", {"organ_function": {"hematologic": {"anc": 3.0}}}, "unverified"),
    ("hematologic_baseline", {}, "unverified"),
    ("hepatic_baseline", {"organ_function": {"hepatic": {"alt_uln": 6}}}, "fail"),
    ("hepatic_baseline", {"organ_function": {"hepatic": {"alt_u_l": 120}}}, "unverified"),
    ("hepatic_baseline", {"organ_function": {"hepatic": {"alt_uln": 1.2, "bilirubin_uln": 0.8}}}, "pass"),
    ("hepatic_baseline", {"organ_function": {"hepatic": {"bilirubin_umol_l": 28}}}, "unverified"),
    ("hepatic_baseline", {"organ_function": {"hepatic": {"bilirubin_umol_l": 50}}}, "fail"),
    ("lvef_baseline", {"organ_function": {"cardiac": {"lvef_pct": 45}}}, "fail"),
    ("lvef_baseline", {"organ_function": {"cardiac": {"lvef_pct": 60}}}, "pass"),
    ("lvef_baseline", {"organ_function": {"cardiac": {"nyha": 3}}}, "fail"),
    ("renal_cisplatin", {"organ_function": {"renal": {"crcl_ml_min": 55}}}, "unverified"),
    ("renal_cisplatin", {"organ_function": {"renal": {"crcl_ml_min": 75}}}, "pass"),
    ("pulmonary_reserve", {"organ_function": {"pulmonary": {"fev1_pct": 55, "dlco_pct": 38}}}, "unverified"),
    ("pulmonary_reserve", {"organ_function": {"ppo_fev1_pct": 70}}, "pass"),
    ("hearing_neuropathy_baseline", {"comorbidities": {"peripheral_neuropathy": 2}}, "unverified"),
    ("hearing_neuropathy_baseline", {"comorbidities": {"hearing_loss": False,
                                                       "peripheral_neuropathy": 1}}, "pass"),
])
def test_gate_readings(gate, facts, status):
    assert evaluate_gate(gate, facts)["status"] == status


def test_component_implied_gates_cover_conditional_chemotherapy():
    by_id = {r.regimen_id: regimen_gates(r) for r in regimen_lib.REGIMENS}
    assert "hematologic_baseline" in by_id["pembro_carbo_taxane"]  # "paclitaxel or nab-paclitaxel"
    assert "hematologic_baseline" in by_id["ccrt_60gy"]
    assert "lvef_baseline" in by_id["tdxd_subsequent_line"]
    assert "lvef_baseline" in by_id["dabrafenib_trametinib_first_line"]
    assert "renal_cisplatin" in by_id["adjuvant_platinum_doublet"]
    assert "hematologic_baseline" not in by_id["osimertinib_first_line"]


def test_low_counts_hold_the_dose_not_the_plan():
    facts = {"driver_mutations": {"egfr": "negative", "alk": "negative"},
             "histologic_category": "adenocarcinoma",
             "organ_function": {"hematologic": {"anc": 0.9, "plt": 60}}}
    found = rules.check_plan({"stage_group": "IVA"}, {"tnm": {}, **facts},
                             {"regimen_ids": ["pembro_pemetrexed_platinum"], "options": []})
    assert not any(v.rule_id == "ORGAN_FUNCTION_GATE" for v in found)


def test_lvef_blocks_tdxd():
    facts = {"tnm": {}, "driver_mutations": {"erbb2": "YVMA insertion", "egfr": "negative",
                                             "alk": "negative"},
             "organ_function": {"cardiac": {"lvef_pct": 40}}, "comorbidities": {"ild": False},
             "treatment_history": [{"line": 1, "agents": ["carboplatin", "pemetrexed"],
                                    "status": "progression"}]}
    found = rules.check_plan({"stage_group": "IVB"}, facts,
                             {"regimen_ids": ["tdxd_subsequent_line"], "options": []})
    assert any(v.rule_id == "ORGAN_FUNCTION_GATE" and "LVEF" in v.message for v in found)


def test_supportive_care_from_labs():
    notes = lab_notes({"organ_function": {"electrolytes": {"calcium_mmol_l": 3.6}},
                       "comorbidities": {"hbv": True}})
    assert notes[0].startswith("高钙血症") and "急症" in notes[0]
    assert any("恩替卡韦" in n for n in notes)


def test_governed_reply_shows_sites_and_supportive_care():
    result = ConsultationSession(role="oncologist").turn(CASE)
    plan = result.state.outputs["treatment_plan"]
    assert plan["metastatic_sites"]["suggested_m"] == "M1c2"
    assert any(n.startswith("骨转移") for n in plan["supportive_care"])
    assert "转移部位：骨（多发）" in result.reply and "支持治疗" in result.reply
    patient = ConsultationSession(role="patient").turn(CASE)
    assert "转移部位" not in patient.reply and "生物标志物分类" not in patient.reply


def test_histology_not_otherwise_specified():
    assert extract("非小细胞肺癌，非特指型（NSCLC-NOS）")["histologic_category"] == "nsclc_nos"
    assert extract("大细胞癌")["histologic_category"] == "large_cell"
    assert "histologic_category" not in extract("NSCLC，EGFR 阴性")
