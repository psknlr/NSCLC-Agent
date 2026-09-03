"""End-to-end tests for the agent orchestrator (offline, mock provider)."""

import json
from pathlib import Path

import pytest

from nsclc_agent import NSCLCAgent, Case, load_config

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "cases"


@pytest.fixture
def agent():
    return NSCLCAgent(load_config())  # built-in mock config


def test_run_stage3b_case(agent):
    case = Case(case_id="t", t="T2b", n="N2b", m="M0",
                presentation="test", question="plan?")
    result = agent.run(case)
    assert result.error is None
    assert result.staging["stage_group"] == "IIIB"
    assert result.module_key == "stage3b"
    assert result.provider == "mock"
    assert result.response is not None
    data = json.loads(result.response.content)
    assert data["_mock"] is True


def test_dry_run_assembles_prompt_without_model(agent):
    case = Case(t="T4", n="N3", m="M0")
    result = agent.run(case, dry_run=True)
    assert "DRY_RUN" in result.flags
    assert result.module_key == "stage3c"
    assert result.provider is None


def test_stage_mismatch_flagged(agent):
    case = Case(t="T2b", n="N2b", m="M0", stage_group="IIIA")
    result = agent.run(case, dry_run=True)
    assert any("STAGE_MISMATCH" in f for f in result.flags)
    # computed value wins
    assert result.staging["stage_group"] == "IIIB"


def test_iiia_routes_to_stage3a(agent):
    case = Case(t="T3", n="N2a", m="M0")  # stage IIIA
    result = agent.run(case, dry_run=True)
    assert result.staging["stage_group"] == "IIIA"
    assert result.module_key == "stage3a"
    assert not any("FALLBACK" in f for f in result.flags)


def test_stage1_routes(agent):
    case = Case(t="T1a", n="N0", m="M0")  # stage IA1
    result = agent.run(case, dry_run=True)
    assert result.staging["stage_group"] == "IA1"
    assert result.module_key == "stage1"


def test_occult_has_no_module(agent):
    case = Case(t="TX", n="N0", m="M0")  # occult carcinoma, no module
    result = agent.run(case, dry_run=True)
    assert result.staging["stage_group"] == "Occult"
    assert result.module_key is None
    assert result.error is not None


def test_unresolved_stage_errors(agent):
    case = Case(presentation="no staging info")
    result = agent.run(case)
    assert result.error is not None
    assert any("STAGE_UNRESOLVED" in f for f in result.flags)


def test_bad_tnm_flagged(agent):
    case = Case(t="T2", n="N2", m="M0")  # ambiguous
    result = agent.run(case)
    assert result.error is not None
    assert any("STAGING_ERROR" in f for f in result.flags)


def test_staging_preamble_injected(agent):
    case = Case(t="T2b", n="N2b", m="M0")
    stage_result, _ = agent.resolve_stage(case)
    from nsclc_agent.prompts import load_module
    module = load_module("stage3b")
    messages = agent.build_messages(case, module, stage_result)
    system = messages[0].content
    assert "DETERMINISTIC STAGING" in system
    assert "Stage group: IIIB" in system
    assert messages[1].role == "user"


@pytest.mark.parametrize("case_file", sorted(EXAMPLES.glob("*.json")))
def test_example_cases_route_and_run(agent, case_file):
    case = Case.from_dict(json.loads(case_file.read_text()))
    result = agent.run(case)
    assert result.error is None, f"{case_file.name}: {result.error}"
    assert result.module_key is not None
    assert result.response is not None


# --- silent-assumption discipline -----------------------------------------

def test_missing_m_is_flagged_not_silently_m0(agent):
    """Staging without an M category must never look like a verified M0."""
    result = agent.run(Case(t="T3", n="N2b"), dry_run=True)
    assert result.staging["stage_group"] == "IIIB"
    assert any(f.startswith("M_ASSUMED_M0") for f in result.flags)
    assert any("assumed" in n for n in result.staging["descriptor_notes"])


def test_explicit_m0_is_not_flagged(agent):
    result = agent.run(Case(t="T3", n="N2b", m="M0"), dry_run=True)
    assert not any(f.startswith("M_ASSUMED_M0") for f in result.flags)


def test_bare_t1_reaches_the_user_as_a_staging_error(agent):
    result = agent.run(Case(t="T1", n="N0", m="M0"))
    assert result.error is not None
    assert any("STAGING_ERROR" in f and "T1a" in f for f in result.flags)


# --- stage-label handling --------------------------------------------------

def test_less_specific_label_is_not_a_mismatch(agent):
    """'IA' vs a computed 'IA1' is a coarser label, not a contradiction."""
    result = agent.run(Case(t="T1a", n="N0", m="M0", stage_group="IA"),
                       dry_run=True)
    assert not any("STAGE_MISMATCH" in f for f in result.flags)


def test_real_mismatch_still_flagged(agent):
    result = agent.run(Case(t="T2b", n="N2b", m="M0", stage_group="IIIA"),
                       dry_run=True)
    assert any("STAGE_MISMATCH" in f for f in result.flags)


def test_freeform_stage_label_routes(agent):
    result = agent.run(Case(stage_group="stage 3b"), dry_run=True)
    assert result.module_key == "stage3b"
    assert any("STAGE_FROM_LABEL" in f and "normalized" in f
               for f in result.flags)


def test_unrecognizable_stage_label_reports_clearly(agent):
    result = agent.run(Case(stage_group="not-a-stage"), dry_run=True)
    assert result.error is not None
    assert any("STAGE_LABEL_UNRECOGNIZED" in f for f in result.flags)


def test_ambiguous_family_label_refused(agent):
    result = agent.run(Case(stage_group="IV"), dry_run=True)
    assert result.module_key is None
    assert any("MODULE_UNAVAILABLE" in f for f in result.flags)


def test_routing_dict_is_serializable(agent):
    """result.routing must be a plain dict copy, not RouteResult.__dict__."""
    result = agent.run(Case(t="T2b", n="N2b", m="M0"), dry_run=True)
    json.dumps(result.routing)
    result.routing["module_key"] = "tampered"
    assert result.module_key == "stage3b"


# --- blank vs absent descriptors -------------------------------------------

@pytest.mark.parametrize("blank", [None, "", "  ", "\t"])
def test_blank_m_is_absent_not_malformed(agent, blank):
    """"" and "  " must take the same path: absent, so M0 is assumed and said."""
    result = agent.run(Case(t="T2b", n="N2b", m=blank), dry_run=True)
    assert result.error is None
    assert result.staging["stage_group"] == "IIIB"
    assert any(f.startswith("M_ASSUMED_M0") for f in result.flags)


@pytest.mark.parametrize("blank", ["", "  "])
def test_blank_t_is_unresolved_not_a_staging_error(agent, blank):
    result = agent.run(Case(t=blank, n="N2b", m="M0"), dry_run=True)
    assert any("STAGE_UNRESOLVED" in f for f in result.flags)


def test_surrounding_whitespace_is_tolerated(agent):
    result = agent.run(Case(t=" T2b ", n="N2b", m=" M0 ",
                            stage_group="  IIIB  "), dry_run=True)
    assert result.staging["stage_group"] == "IIIB"
    assert not any("MISMATCH" in f for f in result.flags)
