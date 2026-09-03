"""Tests for the autonomous consultation loop (自主问诊) — fully offline.

The consultation must work without a model: pass 1 of the extractor is
deterministic, and the planner is pure Python. These tests therefore run on the
built-in mock config and assert the behaviour that makes the loop a
consultation rather than a form — that it asks the blocking questions first,
that each question carries the decision it changes, that it stops when nothing
left can move the recommendation, and that what it never learned is reported
rather than assumed.
"""

import json

import pytest

from nsclc_agent import Case, NSCLCAgent, load_config
from nsclc_agent.consult import (
    STATUS_EXHAUSTED,
    STATUS_GATHERING,
    STATUS_READY,
    ConsultSession,
    extract_deterministic,
    is_sufficient,
    next_questions,
    rank_slots,
    stage_band,
)
from nsclc_agent.consult.slots import SLOTS_BY_KEY


@pytest.fixture
def agent():
    return NSCLCAgent(load_config(), lang="zh")


# --------------------------------------------------------------------------- #
# Slot schema                                                                 #
# --------------------------------------------------------------------------- #

def test_every_slot_states_what_it_decides():
    for slot in SLOTS_BY_KEY.values():
        assert slot.impact_en.strip(), f"{slot.key} has no English impact"
        assert slot.impact_zh.strip(), f"{slot.key} has no Chinese impact"
        assert slot.question_en.strip() and slot.question_zh.strip()


@pytest.mark.parametrize("stage,band", [
    ("IA1", "I"), ("IB", "I"), ("IIA", "II"), ("IIIA", "III"),
    ("IIIC", "III"), ("IVA", "IV"), ("IVB", "IV"), ("0", "0"),
    ("Occult", "pre"), (None, "pre"),
])
def test_stage_band_mapping(stage, band):
    assert stage_band(stage) == band


def test_pd_l1_is_not_decision_relevant_in_stage_one():
    """The stage I module says so explicitly; the weights must agree."""
    pd_l1 = SLOTS_BY_KEY["pd_l1"]
    assert pd_l1.weight("I") < 70 < pd_l1.weight("IV")
    assert pd_l1.weight("III") >= 70


def test_egfr_matters_more_once_the_stage_is_known():
    egfr = SLOTS_BY_KEY["egfr"]
    assert egfr.weight("I") < egfr.weight("II") < egfr.weight("III")


def test_squamous_histology_suppresses_driver_questions():
    known = {"histology": "squamous"}
    keys = [r.key for r in rank_slots(known, "IV")]
    assert "egfr" not in keys and "alk" not in keys


def test_gated_slot_appears_only_once_its_precondition_holds():
    """ccrt_feasibility is meaningless until the tumour is unresectable."""
    assert "ccrt_feasibility" not in [r.key for r in rank_slots({}, "IIIB")]
    known = {"resectability": "unresectable"}
    assert "ccrt_feasibility" in [r.key for r in rank_slots(known, "IIIB")]


# --------------------------------------------------------------------------- #
# Planner: ordering and stopping                                              #
# --------------------------------------------------------------------------- #

def test_staging_descriptors_block_everything_else():
    batch = next_questions({}, None)
    assert {q.key for q in batch} == {"t_category", "n_category", "m_category"}
    assert all(q.blocking for q in batch)


def test_once_staged_the_batch_is_stage_weighted():
    known = {"t_category": "T2b", "n_category": "N2b", "m_category": "M0"}
    keys = [q.key for q in next_questions(known, "IIIB", limit=3)]
    assert "histology" in keys or "egfr" in keys
    assert "t_category" not in keys


def test_batch_size_is_respected():
    known = {"t_category": "T2b", "n_category": "N2b", "m_category": "M0"}
    assert len(next_questions(known, "IIIB", limit=2)) == 2


def test_ranking_is_deterministic():
    known = {"t_category": "T2b", "n_category": "N2b", "m_category": "M0"}
    first = [r.key for r in rank_slots(known, "IIIB")]
    second = [r.key for r in rank_slots(known, "IIIB")]
    assert first == second


def test_sufficiency_is_reached_when_nothing_can_change_the_answer():
    known = {
        "t_category": "T2b", "n_category": "N2b", "m_category": "M0",
        "histology": "adenocarcinoma", "ecog_ps": 1, "egfr": "negative",
        "alk": "negative", "pd_l1": 40, "resectability": "unresectable",
        "ccrt_feasibility": "feasible", "brain_imaging": "MRI, negative",
        "comorbidities": "none", "prior_treatment": "none", "symptoms": "cough",
        "other_drivers": "NGS negative", "goals_of_care": "maximal control",
    }
    assert is_sufficient(known, "IIIB")


def test_incomplete_knowledge_is_not_sufficient():
    assert not is_sufficient({"t_category": "T2b"}, None)


# --------------------------------------------------------------------------- #
# Deterministic extraction                                                    #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("text,expected", [
    ("cT2bN2bM0", {"t_category": "T2b", "n_category": "N2b",
                   "m_category": "M0"}),
    ("ypT1bN0M0 术后", {"t_category": "T1b", "n_category": "N0",
                        "m_category": "M0"}),
    ("T1c N0 M0", {"t_category": "T1c", "n_category": "N0",
                   "m_category": "M0"}),
    ("ECOG 1 分", {"ecog_ps": 1}),
    ("ECOG PS 2", {"ecog_ps": 2}),
    ("PD-L1 TPS 40%", {"pd_l1": 40}),
    ("PD-L1 阴性", {"pd_l1": "<1%"}),
    ("68 岁女性", {"age": 68}),
    ("腺癌", {"histology": "adenocarcinoma"}),
    ("鳞癌", {"histology": "squamous"}),
    ("MDT 认为不可切除", {"resectability": "unresectable"}),
    ("从不吸烟", {"smoking": "never"}),
    ("EGFR 阴性", {"egfr": "negative"}),
    ("EGFR 未做", {"egfr": "not_tested"}),
    ("没有远处转移", {"m_category": "M0"}),
    ("恶性胸腔积液", {"m_category": "M1a"}),
])
def test_deterministic_patterns(text, expected):
    values, _ = extract_deterministic(text)
    for key, want in expected.items():
        assert values.get(key) == want, f"{text!r} -> {values}"


def test_ambiguous_descriptors_are_noted_never_canonicalised():
    """"N2" must not silently become N2a — same rule as the staging engine."""
    values, notes = extract_deterministic("分期 T2 N2 M0")
    assert "t_category" not in values
    assert "n_category" not in values
    assert sum("AMBIGUOUS_DESCRIPTOR" in n for n in notes) == 2


def test_named_metastatic_site_does_not_become_an_m_category():
    values, notes = extract_deterministic("骨转移多处")
    assert "m_category" not in values
    assert any("AMBIGUOUS_DESCRIPTOR" in n for n in notes)


def test_station_numbers_read_only_from_nodal_sentences():
    values, _ = extract_deterministic("纵隔 4L 和 7 组淋巴结转移")
    assert values["n2_stations"] == ["4L", "7"]
    values, _ = extract_deterministic("EBUS: stations 4R, 7 and 10L")
    assert values["n2_stations"] == ["4R", "7", "10L"]
    # A count of nodes is not a station number.
    values, _ = extract_deterministic("淋巴结共 2 个阳性，7 组受累")
    assert values["n2_stations"] == ["7"]


def test_driver_result_does_not_swallow_the_next_clause():
    values, _ = extract_deterministic("EGFR 19del 阳性，PD-L1 TPS 5%")
    assert "PD-L1" not in str(values["egfr"])
    assert values["pd_l1"] == 5


def test_empty_reply_extracts_nothing():
    assert extract_deterministic("") == ({}, [])
    assert extract_deterministic("   ") == ({}, [])


# --------------------------------------------------------------------------- #
# Session mechanics                                                           #
# --------------------------------------------------------------------------- #

def test_opening_statement_is_mined_before_the_first_question(agent):
    session = agent.start_consult(
        presentation="68岁女性，腺癌，T2b N2b M0。")
    assert session.known["t_category"] == "T2b"
    assert session.known["histology"] == "adenocarcinoma"
    assert session.stage_group == "IIIB"
    # The first question must not re-ask what the opening already stated.
    assert "t_category" not in [q["key"] for q in session.ask()]


def test_a_later_reply_can_correct_an_earlier_fact(agent):
    """A clinician correcting themselves must be heard, and it must be logged.

    Regression: `record` skipped any key already present, so the first
    extraction was permanent — including a wrong one — and the correcting
    round was reported back as "no new facts".
    """
    session = agent.start_consult(presentation="T2b N2b M0")
    agent.consult_step(session, "更正一下：其实是 T3。")
    assert session.known["t_category"] == "T3"
    assert session.turns[-1].extracted["t_category"] == "T3"
    assert any("FACT_CORRECTED[t_category]" in n for n in session.notes)
    assert session.provenance["t_category"]["corrected_from"] == "T2b"


def test_a_correction_re_stages_the_case(agent):
    session = agent.start_consult(presentation="腺癌 T2b N2a M0")
    assert session.stage_group == "IIIA"
    agent.consult_step(session, "更正：纵隔是多站的，N2b。")
    assert session.known["n_category"] == "N2b"
    assert session.stage_group == "IIIB"


def test_two_readings_of_one_reply_do_not_fight(agent):
    """Within a round the deterministic pass runs first and keeps its value."""
    session = agent.start_consult(presentation="")
    agent.consult_step(session, "T2b N2b M0")
    before = dict(session.known)
    session.record({"t_category": "T4"}, source="reply",
                   round_index=session.round_index - 1)
    assert session.known["t_category"] == before["t_category"]


def test_provenance_records_where_each_fact_came_from(agent):
    session = agent.start_consult(presentation="T2b N2b M0")
    agent.consult_step(session, "腺癌")
    assert session.provenance["t_category"]["source"] == "opening"
    assert session.provenance["histology"]["source"] == "reply"
    assert session.provenance["histology"]["round"] == 0


def test_a_round_that_yields_nothing_is_still_recorded(agent):
    session = agent.start_consult(presentation="")
    agent.consult_step(session, "我不知道。")
    assert session.round_index == 1
    assert session.turns[0].extracted == {}
    assert any("NO_NEW_FACTS" in n for n in session.turns[0].notes)


def test_rounds_are_bounded_and_exhaustion_is_reported(agent):
    session = agent.start_consult(presentation="", max_rounds=2)
    for _ in range(3):
        agent.consult_step(session, "不清楚")
    assert session.round_index == 2
    assert session.status == STATUS_EXHAUSTED
    assert any("CONSULT_ROUNDS_EXHAUSTED" in n for n in session.notes)


def test_finished_session_ignores_further_replies(agent):
    session = agent.start_consult(presentation="", max_rounds=1)
    agent.consult_step(session, "不清楚")
    assert session.is_finished()
    agent.consult_step(session, "T2b N2b M0")
    assert session.round_index == 1


def test_session_round_trips_through_json(agent):
    session = agent.start_consult(presentation="68岁，腺癌 T2b N2b M0。")
    agent.consult_step(session, "ECOG 1，EGFR 阴性")
    restored = ConsultSession.from_dict(
        json.loads(json.dumps(session.to_dict(), ensure_ascii=False)))
    assert restored.known == session.known
    assert restored.round_index == session.round_index
    assert restored.provenance == session.provenance


def test_session_seeded_from_an_existing_case(agent):
    case = Case(case_id="c1", t="T2b", n="N2b", m="M0",
                presentation="known case", question="plan?",
                fields={"histology": "adenocarcinoma", "unrelated": 1})
    session = agent.start_consult(case=case)
    assert session.known["t_category"] == "T2b"
    assert session.known["histology"] == "adenocarcinoma"
    assert "unrelated" not in session.known
    assert session.case_id == "c1"


def test_transcript_reaches_the_assembled_case(agent):
    session = agent.start_consult(presentation="68岁女性。")
    agent.consult_step(session, "腺癌，T2b N2b M0")
    case = session.to_case()
    assert case.t == "T2b"
    assert "Consultation transcript" in case.presentation
    assert "腺癌" in case.presentation


# --------------------------------------------------------------------------- #
# End to end                                                                  #
# --------------------------------------------------------------------------- #

def test_consultation_reaches_a_routed_recommendation(agent):
    session = agent.start_consult(presentation="68岁女性，左上肺占位。",
                                  question="治疗路径？")
    for reply in [
        "腺癌，T2b，纵隔 4L 和 7 组多站淋巴结转移 N2b，PET-CT 和头颅 MRI 没有远处转移。",
        "ECOG 1 分，EGFR 阴性，ALK 阴性，PD-L1 TPS 40%。",
        "MDT 认为不可切除。",
    ]:
        agent.consult_step(session, reply)
    assert session.stage_group == "IIIB"
    result = agent.finish_consult(session, dry_run=True)
    assert result.module_key == "stage3b"
    assert result.staging["stage_group"] == "IIIB"


def test_unanswered_questions_are_carried_into_the_result(agent):
    session = agent.start_consult(presentation="腺癌 T2b N2b M0", max_rounds=1)
    agent.consult_step(session, "不清楚")
    result = agent.finish_consult(session, dry_run=True)
    assert any(f.startswith("CONSULT_INCOMPLETE") for f in result.flags)
    outstanding = [g for g in result.consult["outstanding"]
                   if g["above_threshold"]]
    assert outstanding, "unanswered decision-relevant slots must be reported"
    assert any(g["key"] == "ecog_ps" for g in outstanding)


def test_consultation_provenance_reaches_the_prompt(agent):
    from nsclc_agent.prompts import load_module
    session = agent.start_consult(presentation="腺癌 T2b N2b M0")
    stage_result, _ = agent.resolve_stage(session.to_case())
    messages = agent.build_messages(
        session.to_case(), load_module("stage3b"), stage_result,
        session=session, lang="zh")
    user = messages[1].content
    assert "CONSULTATION PROVENANCE" in user
    assert "STILL UNKNOWN" in user


def test_chinese_language_override_is_injected(agent):
    from nsclc_agent.prompts import load_module
    stage_result, _ = agent.resolve_stage(Case(t="T2b", n="N2b", m="M0"))
    system_zh = agent.build_messages(
        Case(t="T2b", n="N2b", m="M0"), load_module("stage3b"), stage_result,
        lang="zh")[0].content
    # The modules *mention* the override in their English requirement; the
    # block itself starts with its own banner.
    assert "=== OUTPUT LANGUAGE OVERRIDE" in system_zh
    system_en = agent.build_messages(
        Case(t="T2b", n="N2b", m="M0"), load_module("stage3b"), stage_result,
        lang="en")[0].content
    assert "=== OUTPUT LANGUAGE OVERRIDE" not in system_en


def test_every_module_defers_to_the_language_override():
    """The override only works if the module's own rule yields to it."""
    from nsclc_agent.prompts import list_modules
    for m in list_modules():
        assert "OUTPUT LANGUAGE OVERRIDE" in m.system_prompt, m.key


_CJK = range(0x4E00, 0x9FFF)


def test_english_consultation_asks_in_english(agent):
    session = agent.start_consult(presentation="", lang="en")
    for q in session.ask():
        assert not any(ord(ch) in _CJK for ch in q["question"]), q["question"]
        assert not any(ord(ch) in _CJK for ch in q["why"]), q["why"]


def test_chinese_consultation_asks_in_chinese(agent):
    session = agent.start_consult(presentation="", lang="zh")
    for q in session.ask():
        assert any(ord(ch) in _CJK for ch in q["question"]), q["question"]


# --- extraction false positives (regressions) ------------------------------

@pytest.mark.parametrize("text", [
    "Adenocarcinoma on repeat biopsy. 4.5 cm RLL mass, so T2b.",
    "biopsy showed 3 foci",
    "relapse 4 months later",
    "autopsy series 2 cases",
])
def test_ps_is_not_matched_inside_ordinary_words(text):
    """Case-insensitive 'PS' hides in 'biopsy' — and would read as ECOG 4."""
    assert "ecog_ps" not in extract_deterministic(text)[0]


@pytest.mark.parametrize("text,expected", [
    ("ECOG 1", 1), ("ecog ps 2", 2), ("ECOG PS 3", 3), ("PS 2", 2),
    ("体力状态 1 分", 1),
])
def test_ecog_still_matches_real_statements(text, expected):
    assert extract_deterministic(text)[0]["ecog_ps"] == expected


def test_stations_survive_dash_separated_lists():
    values, _ = extract_deterministic("EBUS sampled 4R and 7 — both positive")
    assert values["n2_stations"] == ["4R", "7"]


def test_whole_body_workup_establishes_m0():
    assert extract_deterministic("PET-CT and brain MRI negative")[0][
        "m_category"] == "M0"


def test_brain_imaging_alone_does_not_establish_m0():
    """A negative brain MRI excludes brain metastases, not bone or liver."""
    assert "m_category" not in extract_deterministic("brain MRI negative")[0]


# --- stage labels without a TNM triple -------------------------------------

def test_a_stage_label_survives_the_consultation(agent):
    """A referral saying "IIIB" with no TNM must still be answerable.

    Regression: the label was dropped when seeding a session from a case, so a
    case that `run` handled fine became unanswerable through `consult`.
    """
    case = Case(case_id="ref", stage_group="IIIB",
                presentation="Stage IIIB on the referral letter; TNM not recorded.")
    session = agent.start_consult(case=case)
    assert session.stage_group_label == "IIIB"
    assert session.stage_group == "IIIB"      # resolved from the label
    assert session.can_be_answered()
    result = agent.finish_consult(session, dry_run=True)
    assert result.module_key == "stage3b"
    assert any("STAGE_FROM_LABEL" in f for f in result.flags)


def test_a_label_does_not_masquerade_as_a_computed_stage(agent):
    case = Case(stage_group="IIIB")
    session = agent.start_consult(case=case)
    # The planner still wants the descriptors the label cannot supply.
    assert "t_category" in [q["key"] for q in session.ask()]
    assert not session.staging_ready()


def test_a_computed_stage_wins_over_a_stale_label(agent):
    case = Case(stage_group="IIIA", t="T2b", n="N2b", m="M0")
    session = agent.start_consult(case=case)
    assert session.stage_group == "IIIB"
    result = agent.finish_consult(session, dry_run=True)
    assert result.staging["stage_group"] == "IIIB"
    assert any("STAGE_MISMATCH" in f for f in result.flags)


def test_stage_label_survives_serialisation(agent):
    session = agent.start_consult(case=Case(stage_group="IVB"))
    restored = ConsultSession.from_dict(
        json.loads(json.dumps(session.to_dict(), ensure_ascii=False)))
    assert restored.stage_group_label == "IVB"
    assert restored.can_be_answered()


def test_nothing_at_all_is_still_unanswerable(agent):
    session = agent.start_consult(presentation="68F with a cough")
    assert not session.can_be_answered()


def test_the_mock_recognises_a_consultation_extraction_call():
    """The marker must be one object, not two literals that can drift apart."""
    from nsclc_agent.consult.extract import CONSULT_MARKER, _EXTRACTION_PROMPT
    from nsclc_agent.providers.mock import CONSULT_MARKER as MOCK_MARKER
    assert CONSULT_MARKER is MOCK_MARKER
    assert CONSULT_MARKER in _EXTRACTION_PROMPT


def test_model_pass_runs_and_loses_to_the_deterministic_pass():
    """A model may fill gaps but must not overwrite a pattern-matched value."""
    from nsclc_agent.consult.extract import extract
    from nsclc_agent.providers.base import (
        GenerationParams, LLMProvider, LLMResponse,
    )

    class Contradicting(LLMProvider):
        kind = "fake"

        def __init__(self):
            super().__init__("fake", "fake-1", GenerationParams())

        def complete(self, messages, *, params=None):
            return LLMResponse(
                content=json.dumps({
                    "values": {"t_category": "T4", "ecog_ps": 3},
                    "notes": ["model note"],
                }),
                provider=self.name, model=self.model, finish_reason="stop")

    values, notes = extract("T2b, adenocarcinoma", ["t_category", "ecog_ps"],
                            provider=Contradicting())
    assert values["t_category"] == "T2b"   # regex wins
    assert values["ecog_ps"] == 3          # model fills the gap
    assert "model note" in notes


def test_a_broken_extraction_model_does_not_end_the_consultation():
    from nsclc_agent.consult.extract import extract
    from nsclc_agent.providers.base import GenerationParams, LLMProvider

    class Broken(LLMProvider):
        kind = "fake"

        def __init__(self):
            super().__init__("fake", "fake-1", GenerationParams())

        def complete(self, messages, *, params=None):
            raise RuntimeError("backend down")

    values, notes = extract("T2b", ["ecog_ps"], provider=Broken())
    assert values["t_category"] == "T2b"
    assert any("EXTRACTION_MODEL_FAILED" in n for n in notes)


def test_attached_films_make_a_session_worth_running(agent):
    """The perception layer can supply descriptors the interview never got."""
    session = agent.start_consult(presentation="68F, LUL mass")
    assert not session.can_be_answered()
    session.images = ["scan.png"]
    assert session.can_be_answered()


def test_films_seed_the_stage_a_consultation_could_not(tmp_path):
    import base64
    from nsclc_agent.providers.base import (
        GenerationParams, LLMProvider, LLMResponse,
    )

    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8z8BQ"
        "DwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
    img = tmp_path / "s.png"
    img.write_bytes(png)

    class FakeVision(LLMProvider):
        kind = "fake-vision"

        def __init__(self):
            super().__init__("fv", "fv-1", GenerationParams())
            self.supports_vision = True

        def complete(self, messages, *, params=None):
            return LLMResponse(
                content=json.dumps({"candidate_t": "T2b",
                                    "candidate_n": "N2b",
                                    "candidate_m": "M0"}),
                provider=self.name, model=self.model, finish_reason="stop")

    agent = NSCLCAgent(load_config(), lang="zh")
    agent._provider_cache["fv"] = FakeVision()
    agent.vision_provider = "fv"

    session = agent.start_consult(presentation="68岁女性，左上肺占位，附 PET-CT。")
    session.images = [str(img)]
    result = agent.finish_consult(session)
    assert result.staging["stage_group"] == "IIIB"
    assert result.imaging["status"] == "MODEL_PROPOSED_UNVERIFIED"
    assert any("RADIOGRAPHIC_TNM_PROPOSED" in f for f in result.flags)
    # Film-derived descriptors are imaging provenance, not interview facts.
    assert "t_category" not in result.consult["known"]


# ===========================================================================
# Regressions from the independent review of this subsystem. Each of these
# produced a silently wrong clinical value, not a crash.
# ===========================================================================

@pytest.mark.parametrize("text", [
    "PET-CT: no distant metastasis, no malignant pleural effusion.",
    "PET-CT 未见远处转移，无恶性胸腔积液。",
    "No pleural nodules and no pericardial involvement on CT.",
    "未见胸膜转移，未见心包转移。",
])
def test_a_negated_finding_does_not_set_m1(text):
    """"no malignant pleural effusion" set M1a — staging a curative case IVA."""
    values, _ = extract_deterministic(text)
    assert values.get("m_category") != "M1a"


def test_a_real_m1a_finding_still_sets_m1a():
    values, _ = extract_deterministic("CT shows a malignant pleural effusion.")
    assert values["m_category"] == "M1a"


def test_negated_case_does_not_route_to_the_metastatic_module(agent):
    session = agent.start_consult(presentation=(
        "65M, RUL adenocarcinoma, cT2b, multi-station mediastinal nodes N2b. "
        "PET-CT: no distant metastasis, no malignant pleural effusion. "
        "Brain MRI normal. ECOG 1."))
    assert session.stage_group == "IIIB"
    assert agent.finish_consult(session, dry_run=True).module_key == "stage3b"


@pytest.mark.parametrize("text", [
    "EGFR 19del positive / ALK negative / ROS1 negative",
    "基因检测：EGFR 19del 阳性 ALK 阴性 ROS1 阴性",
    "NGS: EGFR exon 19 deletion detected ALK not detected",
])
def test_one_genes_result_does_not_bleed_into_another(text):
    """An EGFR-mutant patient was being recorded as EGFR-negative."""
    values, _ = extract_deterministic(text)
    assert "negative" not in str(values.get("egfr", "")).lower()
    assert values.get("alk") in ("negative", "not_tested")


@pytest.mark.parametrize("text,expected", [
    ("MDT deemed the tumour inoperable because of poor cardiac reserve.",
     ("unresectable", "medically_inoperable")),
    ("He is considered inoperable; definitive chemoradiation is planned.",
     ("unresectable", "medically_inoperable")),
    ("The tumour is not resectable.", ("unresectable",)),
    ("MDT considers it resectable.", ("resectable",)),
    ("Medically operable and technically resectable.", ("resectable",)),
    ("Patient refused surgery.", ("declined_surgery",)),
])
def test_resectability_is_not_inverted(text, expected):
    """"operable" matched inside "inoperable", flipping the stage III fork."""
    assert extract_deterministic(text)[0].get("resectability") in expected


def test_inoperable_still_opens_the_ccrt_question():
    known = extract_deterministic("The tumour is inoperable.")[0]
    assert "ccrt_feasibility" in [r.key for r in rank_slots(known, "IIIB")]


@pytest.mark.parametrize("text", [
    "There is no tissue diagnosis yet; the lesion was found on screening CT.",
    "The diagnosis was made at another hospital.",
    "Prognosis was discussed with the family.",
])
def test_histology_is_not_invented_from_diagnosis_or_prognosis(text):
    """"nos" matched inside "diagnosis", and histology then never got asked."""
    assert "histology" not in extract_deterministic(text)[0]


def test_a_real_nsclc_nos_is_still_read():
    assert extract_deterministic("Histology: NSCLC-NOS.")[0]["histology"] == \
        "NSCLC_NOS"


@pytest.mark.parametrize("text", [
    "低剂量CT3年随访", "复查CT3次未见变化", "PET-CT4处高代谢灶",
    "Serial CT4 weeks apart",
])
def test_a_t_descriptor_is_not_invented_from_the_word_ct(text):
    """The 'C' of 'CT' was consumed as a staging prefix."""
    assert "t_category" not in extract_deterministic(text)[0]


@pytest.mark.parametrize("text,expected", [
    ("cT2b N2b M0", "T2b"), ("CT shows T3 disease", "T3"),
    ("ypT1bN0M0", "T1b"), ("pT4 N0", "T4"),
])
def test_real_descriptors_survive_the_prefix_check(text, expected):
    assert extract_deterministic(text)[0]["t_category"] == expected


@pytest.mark.parametrize("text", [
    "EBUS sampled 3 stations, all negative",
    "N2 disease involving 4 stations",
    "纵隔淋巴结肿大 共 2 组",
])
def test_a_count_of_stations_is_not_a_station_number(text):
    assert not extract_deterministic(text)[0].get("n2_stations")


@pytest.mark.parametrize("text,expected", [
    ("EBUS: station 7 positive", ["7"]),
    ("7 组淋巴结受累", ["7"]),
    ("第 4 组和第 7 组纵隔淋巴结", ["4", "7"]),
])
def test_station_identifiers_are_still_read(text, expected):
    assert extract_deterministic(text)[0]["n2_stations"] == expected


@pytest.mark.parametrize("text", [
    "Brain MRI showed no brain metastases.",
    "No bone metastases on the bone scan.",
    "Liver ultrasound shows no liver metastasis.",
])
def test_english_negation_of_a_site_is_honoured(text):
    """The negation guard existed only in Chinese, so English replies that
    ruled a site out produced an audit note asserting M1."""
    values, notes = extract_deterministic(text)
    assert values.get("m_category") != "M1a"
    assert not any("AMBIGUOUS_DESCRIPTOR" in n and "metastas" in n
                   for n in notes)


@pytest.mark.parametrize("text,expected", [
    ("PD-L1 (22C3) TPS 50%", 50),
    ("PD-L1 22C3 TPS 60%", 60),
    ("PD-L1 TPS 50%", 50),
    ("PD-L1 (SP263) 1%", 1),
])
def test_a_named_assay_does_not_block_the_pd_l1_match(text, expected):
    assert extract_deterministic(text)[0]["pd_l1"] == expected


def test_spelled_out_performance_status_is_read():
    assert extract_deterministic(
        "ECOG performance status 1, no weight loss")[0]["ecog_ps"] == 1


def test_an_ecog_range_records_the_worse_end_and_says_so():
    values, notes = extract_deterministic("ECOG 0-1")
    assert values["ecog_ps"] == 1
    assert any("ECOG_RANGE" in n for n in notes)


# --- validity, not just presence -------------------------------------------

def test_ambiguous_seeded_descriptors_do_not_look_resolved(agent):
    """A case seeded with T2/N2 declared itself ready and never asked again."""
    case = Case.from_dict({"id": "c9", "t": "T2", "n": "N2", "m": "M0",
                           "presentation": "腺癌，ECOG 1"})
    session = agent.start_consult(case=case)
    assert not session.staging_ready()
    assert session.status == STATUS_GATHERING
    asked = [q["key"] for q in session.ask()]
    assert "t_category" in asked and "n_category" in asked


def test_a_model_may_not_supply_an_ambiguous_descriptor():
    from nsclc_agent.consult.extract import extract
    from nsclc_agent.providers.base import (
        GenerationParams, LLMProvider, LLMResponse,
    )

    class Ambiguous(LLMProvider):
        kind = "fake"

        def __init__(self):
            super().__init__("f", "f1", GenerationParams())

        def complete(self, messages, *, params=None):
            return LLMResponse(
                content=json.dumps({"values": {"t_category": "T2",
                                               "n_category": "N2"}}),
                provider="f", model="f1", finish_reason="stop")

    values, notes = extract("no descriptors here",
                            ["t_category", "n_category"],
                            provider=Ambiguous())
    assert "t_category" not in values and "n_category" not in values
    assert sum("MODEL_DESCRIPTOR_REJECTED" in n for n in notes) == 2


# --- _refresh idempotence ---------------------------------------------------

def test_refresh_does_not_duplicate_the_exhausted_note(agent):
    session = agent.start_consult(presentation="", max_rounds=1)
    agent.consult_step(session, "不清楚")
    before = len(session.notes)
    for _ in range(3):
        agent._refresh(session)
    assert len(session.notes) == before


def test_a_completed_session_is_not_dragged_back_into_gathering(agent):
    session = agent.start_consult(presentation="腺癌 T2b N2b M0 ECOG 1")
    agent.finish_consult(session)
    assert session.status == "complete"
    agent._refresh(session)
    assert session.status == "complete"


# ===========================================================================
# Second adversarial pass — realistic phrasing that produced wrong values.
# Convention: a value that would route the case wrongly is the failure that
# matters; leaving a slot unknown is cheap because the consultation asks.
# ===========================================================================

@pytest.mark.parametrize("text", [
    "Non-squamous NSCLC, PD-L1 30%",
    "非鳞状非小细胞肺癌，PD-L1 30%",
    "non-small cell, non-squamous histology",
])
def test_non_squamous_is_not_squamous(text):
    """Inverted the histology that gates driver testing and pemetrexed."""
    assert extract_deterministic(text)[0].get("histology") != "squamous"


def test_squamous_itself_is_still_read():
    assert extract_deterministic("Squamous cell carcinoma on biopsy.")[0][
        "histology"] == "squamous"


@pytest.mark.parametrize("text,expected", [
    ("PD-L1 TPS <1%", "<1%"),
    ("PD-L1 ≥50%", ">=50%"),
    ("PD-L1 小于1%", "<1%"),
    ("PD-L1 (22C3) TPS 50%", 50),
])
def test_pd_l1_comparator_is_kept(text, expected):
    """"<1%" and "1%" sit on opposite sides of the immunotherapy threshold."""
    assert extract_deterministic(text)[0]["pd_l1"] == expected


@pytest.mark.parametrize("text", [
    "Small pericardial effusion, likely benign.",
    "A 4 mm contralateral lung nodule, probably a granuloma.",
    "Trace pleural effusion, not sampled.",
])
def test_a_finding_that_may_be_benign_is_not_m1a(text):
    assert "m_category" not in extract_deterministic(text)[0]


@pytest.mark.parametrize("text", [
    "Malignant pleural effusion confirmed on cytology.",
    "恶性胸腔积液，细胞学阳性。",
    "Pleural metastases on thoracoscopy.",
])
def test_an_explicitly_malignant_finding_is_m1a(text):
    assert extract_deterministic(text)[0]["m_category"] == "M1a"


@pytest.mark.parametrize("text", [
    "PET-CT shows a hypermetabolic bone lesion and brain MRI negative",
    "PET-CT shows bone lesions, brain MRI negative",
    "PET-CT: avid adrenal mass; brain MRI negative",
])
def test_pet_negative_does_not_reach_across_a_positive_finding(text):
    """"PET-CT … negative" was matched across the lesion in between."""
    assert extract_deterministic(text)[0].get("m_category") != "M0"


def test_pet_and_brain_mri_negative_still_reads_m0():
    assert extract_deterministic("PET-CT and brain MRI negative")[0][
        "m_category"] == "M0"


@pytest.mark.parametrize("text,expected", [
    ("非常大的恶性胸腔积液", "M1a"),          # 非常 = "very", not a negation
    ("无症状恶性胸腔积液", "M1a"),            # 无症状 = asymptomatic
    ("denies chest pain but has a malignant pleural effusion", "M1a"),
])
def test_negation_does_not_over_reach(text, expected):
    assert extract_deterministic(text)[0]["m_category"] == expected


@pytest.mark.parametrize("text", [
    "Bone metastases in three vertebrae.",
    "Multiple liver metastases on CT.",
    "Intracranial metastasis, single lesion.",
])
def test_english_site_stems_are_detected(text):
    """Word-bounding the needles had made every English site invisible."""
    _, notes = extract_deterministic(text)
    assert any("AMBIGUOUS_DESCRIPTOR" in n for n in notes)


@pytest.mark.parametrize("text,key,expected", [
    ("smoked for 40 yr", "age", None),
    ("30 pack-years", "age", None),
    ("68女性，腺癌", "age", 68),
    ("患者 72 岁男性", "age", 72),
    ("68F adenocarcinoma", "age", 68),
    ("68 years old", "age", 68),
])
def test_age_is_read_only_from_age_phrasing(text, key, expected):
    assert extract_deterministic(text)[0].get(key) == expected


def test_a_count_of_involved_stations_is_not_a_station():
    assert not extract_deterministic("淋巴结转移 2 组")[0].get("n2_stations")
    assert extract_deterministic("转移至7组淋巴结")[0]["n2_stations"] == ["7"]


@pytest.mark.parametrize("text", [
    "ALK rearranged on FISH", "ALK rearrangement detected",
    "EGFR exon 19 deletion", "EGFR exon 20 insertion",
])
def test_driver_stems_are_read_as_positive(text):
    values, _ = extract_deterministic(text)
    key = "alk" if text.startswith("ALK") else "egfr"
    assert values.get(key) and "negative" not in str(values[key]).lower()


def test_unusable_seeded_descriptors_are_reported_as_missing(agent):
    case = Case.from_dict({"t": "T2", "n": "N2", "m": "M0"})
    session = agent.start_consult(case=case)
    assert set(session.missing_staging_descriptors()) == {"t_category",
                                                          "n_category"}


def test_model_descriptors_are_stored_canonically(agent):
    """"t2b" from a model and "T2b" from a regex must not read as a
    correction of one another."""
    from nsclc_agent.consult.extract import extract
    from nsclc_agent.providers.base import (
        GenerationParams, LLMProvider, LLMResponse,
    )

    class Lower(LLMProvider):
        kind = "fake"

        def __init__(self):
            super().__init__("f", "f1", GenerationParams())

        def complete(self, messages, *, params=None):
            return LLMResponse(
                content=json.dumps({"values": {"n_category": "n2b"}}),
                provider="f", model="f1", finish_reason="stop")

    values, _ = extract("no descriptor", ["n_category"], provider=Lower())
    assert values["n_category"] == "N2b"
