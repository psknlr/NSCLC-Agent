"""The staging engine: stage table, refusal table, prefixes, edition gate."""

import pytest

from nsclc_agent.staging import (
    StagingError, TNM, normalize_stage_group, route, stage_from_strings,
)
from nsclc_agent.staging.selftest import EXPECTATIONS, REJECTIONS, run_selftest


def test_selftest_all_pass():
    passed, total, failures = run_selftest()
    assert failures == []
    assert passed == total == len(EXPECTATIONS) + len(REJECTIONS)


@pytest.mark.parametrize("t,n,m,expected", EXPECTATIONS)
def test_stage_table(t, n, m, expected):
    assert stage_from_strings(t, n, m).stage_group == expected


@pytest.mark.parametrize("t,n,m,needle", REJECTIONS)
def test_refusals_are_actionable(t, n, m, needle):
    with pytest.raises(StagingError) as exc:
        stage_from_strings(t, n, m)
    assert needle.lower() in str(exc.value).lower()


def test_bare_t1_refused_not_coerced():
    """v0.1 silently mapped T1 → T1a, fabricating IA1 precision."""
    with pytest.raises(StagingError, match="Ambiguous 'T1'"):
        stage_from_strings("T1", "N0", "M0")


def test_empty_m_refused_not_defaulted():
    """v0.1's `m or 'M0'` staged unworked-up patients as curable."""
    with pytest.raises(StagingError, match="metastatic workup"):
        stage_from_strings("T2a", "N0", "")


def test_prefix_carried_and_validated():
    result = stage_from_strings("T2a", "N1", "M0", prefix="yp")
    assert str(result.tnm) == "ypT2aN1M0"
    assert any("ypTNM" in note for note in result.descriptor_notes)
    with pytest.raises(StagingError, match="prefix"):
        TNM.parse("T2a", "N1", "M0", prefix="q")


def test_edition_gate_rejects_ajcc8():
    with pytest.raises(StagingError, match="9th edition only"):
        stage_from_strings("T1a", "N1", "M0", edition="AJCC8")


def test_fullwidth_input_normalized():
    assert stage_from_strings("Ｔ２ａ", "N0", "M0").stage_group == "IB"


@pytest.mark.parametrize("label,expected", [
    ("IIIA", "IIIA"), ("Stage IIIA", "IIIA"), ("stage iiib", "IIIB"),
    ("3A", "IIIA"), ("4b", "IVB"), ("IA1", "IA1"), ("ⅢA", "IIIA"),
    ("0", "0"), ("Occult", "Occult"), ("IIIA期", "IIIA"),
])
def test_normalize_stage_group(label, expected):
    assert normalize_stage_group(label) == expected


def test_normalize_stage_group_rejects_garbage():
    with pytest.raises(StagingError):
        normalize_stage_group("stage banana")


def test_all_engine_outputs_route_somewhere():
    """The v0.1 dead ends (stage 0 → wrong module, Occult → error) are gone."""
    groups = {e[3] for e in EXPECTATIONS}
    for group in groups:
        result = route(group)
        assert result.available, f"{group} does not route"
        assert result.module_key
    assert route("0").module_key == "stage0"
    assert route("Occult").module_key == "workup"
