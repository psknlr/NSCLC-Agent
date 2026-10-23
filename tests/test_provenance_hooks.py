"""Provenance and emergency-first hooks over every field of a consult
(v1.4.1 red-team findings): references, doses and outcome statistics in
free text, per-regimen dose checks, and "not an emergency" / "start
osimertinib immediately" no longer read as putting an emergency first."""

import pytest

from nsclc_agent.agentic import hooks, provenance
from nsclc_agent.agentic.session import AgentSession
from nsclc_agent.agentic.toolbox import AgentToolbox

OSIMERTINIB = {"name": "First-line osimertinib", "regimen_ids": ["osimertinib_first_line"],
               "evidence": ["FLAURA"], "rationale": "EGFR L858R, ECOG 1"}
NARRATIVE = ("64-year-old woman, lung adenocarcinoma cT2aN2bM1c2, ECOG 1. EGFR L858R. "
             "PD-L1 TPS 5%.")


def _ctx(consult, *, tools=(), narrative=NARRATIVE, role="oncologist", emergency=None):
    """A stop-hook context; ``tools`` are (name, args) calls whose real results
    go through the evidence ledger first."""
    state = {"evidence_seen": set(), "dosing_seen": set(), "numbers_seen": set(),
             "emergency": emergency}
    box = AgentToolbox()
    for name, args in tools:
        hooks._evidence_ledger({"tool": name, "args": args, "result": box.execute(name, args),
                                "state": state})
    return {"consult": {"options": [OSIMERTINIB], **consult}, "state": state,
            "narrative": narrative, "role": role, "lang": "en", "facts": {}}


def _ids(hook, ctx):
    result = hook(ctx)
    return {f["rule_id"] for f in result.findings} if result else set()


FLAURA_LOOKUP = [("search_trials", {"query": "FLAURA"})]
OSI_DOSING = [("regimen_dosing", {"regimen_id": "osimertinib_first_line"})]


@pytest.mark.parametrize("consult", [
    {"reply": "Osimertinib (FLAURA). Its benefit was confirmed by PHOENIX-LUNG-3."},
    {"reply": "Osimertinib. See PMID 39812345 for the updated survival analysis."},
    {"reply": "Osimertinib.", "assessment": "Eligible for study NCT05999999 at our centre."},
    {"reply": "一线奥希替尼。", "options": [{**OSIMERTINIB, "rationale": "依据 LUNAR-7 研究"}]},
])
def test_citations_in_free_text_are_traced(consult):
    assert "UNVERIFIED_CITATION" in _ids(hooks._citation_provenance, _ctx(consult, tools=FLAURA_LOOKUP))


@pytest.mark.parametrize("consult,narrative", [
    # registry trials, clinical vocabulary that looks like a name
    ({"reply": "FLAURA2 and MARIPOSA are alternatives; see also CheckMate 9LA and KEYNOTE-189. "
               "ECOG 1, AJCC 9th, COVID-19, CTLA-4, QTcF 470 ms, (IIIA), (SBRT), GOLD 3."},
     NARRATIVE),
    # a trial the clinician named
    ({"reply": "Continue on the LUNAR-7 protocol."}, NARRATIVE + " Enrolled in LUNAR-7."),
])
def test_citations_that_resolve_are_quiet(consult, narrative):
    assert "UNVERIFIED_CITATION" not in _ids(hooks._citation_provenance,
                                             _ctx(consult, narrative=narrative))


def test_a_reference_a_tool_returned_in_its_text_is_seen():
    state = {"evidence_seen": set(), "dosing_seen": set(), "numbers_seen": set()}
    hooks._evidence_ledger({"tool": "mcp__pubmed__search", "args": {}, "state": state,
                            "result": {"ok": True, "summary": "1 hit",
                                       "data": "PMID: 39812345 — PHOENIX-LUNG-3 final OS"}})
    ctx = {**_ctx({"reply": "PHOENIX-LUNG-3 (PMID 39812345)."}), "state": state}
    assert "UNVERIFIED_CITATION" not in _ids(hooks._citation_provenance, ctx)


@pytest.mark.parametrize("consult,tools,expected", [
    ({"reply": "Osimertinib.", "warnings": ["Give osimertinib 80 mg daily."]}, (),
     "DOSE_NOT_FROM_LIBRARY"),
    ({"reply": "Osimertinib.", "assessment": "Start osimertinib at 80 mg daily."}, (),
     "DOSE_NOT_FROM_LIBRARY"),
    ({"reply": "Osimertinib; at progression carboplatin AUC 5 plus pemetrexed 500 mg/m2."},
     OSI_DOSING, "DOSE_NOT_FROM_LIBRARY"),
    ({"reply": "Osimertinib 160 mg once daily."}, OSI_DOSING, "DOSE_DIFFERS_FROM_LIBRARY"),
])
def test_doses_are_traced_to_the_regimen_looked_up(consult, tools, expected):
    assert expected in _ids(hooks._dose_provenance, _ctx(consult, tools=tools))


@pytest.mark.parametrize("consult,tools", [
    ({"reply": "Osimertinib 80 mg once daily, library reference dose."}, OSI_DOSING),
    ({"reply": "Concurrent chemoradiation (ccrt_60gy), then durvalumab (PACIFIC)."}, ()),
    ({"reply": "Thoracic RT 60 Gy in 30 fractions with concurrent chemotherapy."},
     [("regimen_dosing", {"regimen_id": "ccrt_60gy"})]),
])
def test_library_doses_are_quiet(consult, tools):
    found = _ids(hooks._dose_provenance, _ctx(consult, tools=tools))
    assert not found & {"DOSE_NOT_FROM_LIBRARY", "DOSE_DIFFERS_FROM_LIBRARY"}


def test_doses_named_in_the_carboplatin_message():
    result = hooks._dose_provenance(_ctx(
        {"reply": "Osimertinib; later carboplatin AUC 5."}, tools=OSI_DOSING))
    message = next(f["message"] for f in result.findings if f["rule_id"] == "DOSE_NOT_FROM_LIBRARY")
    assert "carboplatin" in message and "osimertinib" not in message


@pytest.mark.parametrize("reply,tools,flagged", [
    ("With this approach 5-year overall survival is about 87%.", FLAURA_LOOKUP, True),
    ("FLAURA showed a PFS hazard ratio of 0.21.", FLAURA_LOOKUP, True),
    ("Median OS 38.6 months versus 31.8 months.", (), True),
    ("In FLAURA the PFS HR was 0.46 versus first-generation TKIs.", FLAURA_LOOKUP, False),
    ("64-year-old, ECOG 1, PD-L1 TPS 5%: osimertinib.", (), False),       # the case's own numbers
    ("TPS ≥50% would have qualified for pembrolizumab; review in 3 months.", (), False),
])
def test_outcome_statistics_need_an_anchor(reply, tools, flagged):
    found = _ids(hooks._number_provenance, _ctx({"reply": reply}, tools=tools))
    assert ("UNANCHORED_NUMBER" in found) is flagged


CORD = {"signals": ["脊髓压迫 / cord compression"]}


@pytest.mark.parametrize("consult,flagged", [
    ({"reply": "立即开始奥希替尼一线治疗（FLAURA）。"}, True),
    ({"reply": "This is not an emergency or an urgent matter; continue osimertinib."}, True),
    ({"reply": "Not urgent; review in clinic next month."}, True),
    ({"reply": "无需急诊，门诊随访。"}, True),
    ({"reply": "Emergency: suspected cord compression. Same-day ED assessment, whole-spine MRI."},
     False),
    ({"reply": "Urgent whole-spine MRI and dexamethasone today."}, False),
    ({"reply": "立即急诊：全脊柱MRI，地塞米松。"}, False),
    ({"reply": "Osimertinib.", "intent": "emergency"}, False),
])
def test_emergency_must_be_put_first_not_merely_mentioned(consult, flagged):
    found = _ids(hooks._emergency_addressed, _ctx(consult, emergency=CORD))
    assert ("EMERGENCY_NOT_ADDRESSED" in found) is flagged


class _Model:
    available = True
    supports_vision = False


def test_number_ledger_survives_save_and_load():
    session = AgentSession(_Model(), role="oncologist")
    session.state["numbers_seen"].update({"0.46", "87"})
    resumed = AgentSession.load(session.to_dict(), _Model(), role="oncologist")
    assert resumed.state["numbers_seen"] == {"0.46", "87"}


def test_reference_scan_is_linear_on_long_capitalized_runs():
    text = "A" * 5000 + " (" + "ABCDEFGH" * 400
    assert provenance.references(text) == []
