"""Tool layer: broker policy, circuit breaker, registry behavior, dose channel."""

import pytest

from nsclc_agent.skills import SkillRegistry
from nsclc_agent.state import Budget, EvidenceLevel
from nsclc_agent.tools import CapabilityBroker, ToolHealth, ToolRegistry


@pytest.fixture
def registry():
    return ToolRegistry()


@pytest.fixture
def skills():
    return SkillRegistry.discover()


def _broker(role="oncologist", risk="routine", skill="nsclc.treatment",
            skills_reg=None, budget=None, health=None):
    return CapabilityBroker(
        role, risk, budget=budget, skill_registry=skills_reg,
        active_skill=skill, health=health or ToolHealth(),
    )


# ------------------------------------------------------------------- broker

def test_no_skill_fails_closed(skills):
    broker = _broker(skill=None, skills_reg=skills)
    allowed, reason = broker.allow("trial_lookup")
    assert not allowed and "no_active_skill" in reason


def test_unknown_skill_fails_closed(skills):
    broker = _broker(skill="nsclc.nonexistent", skills_reg=skills)
    allowed, reason = broker.allow("trial_lookup")
    assert not allowed and "unknown_skill" in reason


def test_treatment_skill_cannot_reach_dose_channel(skills):
    broker = _broker(skill="nsclc.treatment", skills_reg=skills)
    assert broker.allow("trial_lookup")[0]
    allowed, reason = broker.allow("regimen_detail")
    assert not allowed and "not_granted" in reason


def test_panel_skill_cannot_reach_dose_channel(skills):
    broker = _broker(skill="nsclc.panel", skills_reg=skills)
    allowed, _ = broker.allow("regimen_detail")
    assert not allowed


def test_patient_role_denied_dose_tools(skills):
    broker = _broker(role="patient", skill="nsclc.dose_planning", skills_reg=skills)
    allowed, reason = broker.allow("regimen_detail")
    assert not allowed
    # Both the role gate and the skill's role restriction stand in the way.
    assert "patient" in reason or "role" in reason


def test_emergency_mode_forbids_planning_tools(skills):
    broker = _broker(risk="emergency", skill="nsclc.treatment", skills_reg=skills)
    allowed, reason = broker.allow("protocol_lookup")
    assert not allowed and "emergency" in reason


def test_denied_call_never_charges_budget(registry, skills):
    budget = Budget(max_tool_calls=5)
    broker = _broker(skill=None, skills_reg=skills, budget=budget)
    registry.call(broker, "trial_lookup", query="ADAURA")
    assert budget.used_tool_calls == 0


def test_executed_call_charges_budget(registry, skills):
    budget = Budget(max_tool_calls=5)
    broker = _broker(skills_reg=skills, budget=budget)
    result = registry.call(broker, "trial_lookup", query="ADAURA")
    assert result.ok
    assert budget.used_tool_calls == 1


def test_circuit_breaker_opens_after_failures():
    health = ToolHealth(failure_threshold=2)
    health.record_failure("x")
    assert health.is_healthy("x")
    health.record_failure("x")
    assert not health.is_healthy("x")
    health.record_success("x")
    assert health.is_healthy("x")


# ------------------------------------------------------------------ registry

def test_stage_lookup_refusal_is_an_answer(registry, skills):
    broker = _broker(skills_reg=skills)
    result = registry.call(broker, "stage_lookup", t="T2a", n="N2", m="M0")
    assert result.ok  # a refusal is a correct answer, not a failure
    assert result.data["staged"] is False
    assert "N2a" in result.data["refusal"]
    assert result.evidence_level == EvidenceLevel.STAGING_ENGINE.value


def test_stage_lookup_stages(registry, skills):
    broker = _broker(skills_reg=skills)
    result = registry.call(broker, "stage_lookup", t="T2b", n="N2b", m="M0")
    assert result.data["staged"] and result.data["stage_group"] == "IIIB"


def test_trial_lookup_alias_resolution(registry, skills):
    broker = _broker(skills_reg=skills)
    for query in ("KEYNOTE-671", "keynote 671", "NCT03425643"):
        result = registry.call(broker, "trial_lookup", query=query)
        assert result.data.get("match") == "exact", query
        assert result.data["trial"]["trial_id"] == "KEYNOTE671"


def test_trial_lookup_search_and_honest_miss(registry, skills):
    broker = _broker(skills_reg=skills)
    hit = registry.call(broker, "trial_lookup", query="osimertinib adjuvant")
    assert any(t["trial_id"] == "ADAURA" for t in hit.data["trials"])
    miss = registry.call(broker, "trial_lookup", query="zzzz_nonexistent")
    assert miss.ok and miss.data["match"] == "none"
    assert "verify" in miss.data["note"]


def test_regimen_lookup_summary_carries_no_doses(registry, skills):
    """The reasoning view of a regimen must be numeric-free."""
    import json
    import re

    broker = _broker(skills_reg=skills)
    result = registry.call(broker, "regimen_lookup", query="pembrolizumab")
    blob = json.dumps(result.data, ensure_ascii=False)
    for rid in ("pembro_perioperative", "pembro_monotherapy",
                "pembro_pemetrexed_platinum"):
        blob = blob.replace(rid, "")
    assert not re.search(r"\d+\s*mg", blob)


def test_regimen_detail_carries_doses(registry, skills):
    broker = _broker(skill="nsclc.dose_planning", skills_reg=skills)
    result = registry.call(broker, "regimen_detail", regimen_id="osimertinib_adjuvant")
    assert result.ok
    assert any("80 mg" in c["dose"] for c in result.data["regimen"]["components"])


def test_protocol_lookup_sections(registry, skills):
    broker = _broker(skills_reg=skills)
    result = registry.call(broker, "protocol_lookup", stage="IIIB",
                           query="durvalumab consolidation")
    assert result.ok and result.data["module"] == "stage3b"
    assert result.data["sections"]
    blob = " ".join(s["text"] for s in result.data["sections"])
    assert "durvalumab" in blob.lower()


def test_guideline_lookup_stub_is_honest(registry, skills):
    broker = _broker(skills_reg=skills)
    result = registry.call(broker, "guideline_lookup", query="stage III NSCLC")
    assert result.is_stub
    assert result.resolved_level() == EvidenceLevel.STUB.value


def test_citation_verify_registry_offline(registry, skills):
    broker = _broker(skills_reg=skills)
    result = registry.call(broker, "citation_verify", reference="PACIFIC")
    assert result.data["verified"] and result.data["method"] == "trial_registry"
    nct = registry.call(broker, "citation_verify", reference="NCT02125461")
    assert nct.data["verified"]


def test_citation_verify_unknown_stays_unverified(registry, skills, monkeypatch):
    monkeypatch.delenv("NSCLC_AGENT_ONLINE", raising=False)
    broker = _broker(skills_reg=skills)
    result = registry.call(broker, "citation_verify", reference="12345678")
    assert result.data["verified"] is False
    garbage = registry.call(broker, "citation_verify", reference="TotallyFakeTrial")
    assert garbage.data["verified"] is False


def test_pubmed_offline_is_stub(registry, skills, monkeypatch):
    monkeypatch.delenv("NSCLC_AGENT_ONLINE", raising=False)
    broker = _broker(skills_reg=skills)
    result = registry.call(broker, "pubmed_search", query="osimertinib")
    assert result.is_stub


def test_interaction_check(registry, skills):
    broker = _broker(skills_reg=skills)
    result = registry.call(broker, "interaction_check",
                           medications=["osimertinib", "rifampin", "warfarin"])
    ids = {h["rule_id"] for h in result.data["hits"]}
    assert "osimertinib_cyp3a4_inducer" in ids
    assert "warfarin_tki" in ids


def test_bad_arguments_recoverable(registry, skills):
    broker = _broker(skills_reg=skills)
    result = registry.call(broker, "trial_lookup", wrong_kwarg="x")
    assert not result.ok and result.recoverable


def test_dose_gate_check(registry, skills):
    broker = _broker(skill="nsclc.dose_planning", skills_reg=skills)
    result = registry.call(
        broker, "dose_gate_check", regimen_id="pembro_perioperative",
        facts={"driver_mutations": {"egfr": "L858R", "alk": "negative"}})
    gates = {g["gate"]: g["status"] for g in result.data["gates"]}
    assert gates["egfr_alk_negative"] == "fail"
    assert result.data["all_clear"] is False
