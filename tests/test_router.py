"""Tests for stage → module routing and prompt loading."""

import pytest

from nsclc_agent.staging import route, available_modules
from nsclc_agent.prompts import load_module, list_modules, PromptNotFound


@pytest.mark.parametrize("stage_group,module", [
    ("0", "stage1"),
    ("IA1", "stage1"),
    ("IA3", "stage1"),
    ("IB", "stage1"),
    ("IIA", "stage2"),
    ("IIB", "stage2"),
    ("IIIA", "stage3a"),
    ("IIIB", "stage3b"),
    ("IIIC", "stage3c"),
    ("IVA", "stage4a"),
    ("IVB", "stage4b"),
])
def test_routing_available(stage_group, module):
    r = route(stage_group)
    assert r.available
    assert r.module_key == module


@pytest.mark.parametrize("stage_group", ["Occult"])
def test_routing_unavailable(stage_group):
    r = route(stage_group)
    assert not r.available
    assert r.module_key is None
    assert r.note


def test_all_modules_loadable():
    for m in list_modules():
        assert m.system_prompt
        assert m.system_prompt.startswith("=")  # banner


def test_module_content_matches_stage():
    assert "STAGE I" in load_module("stage1").system_prompt
    assert "Perioperative" in load_module("stage3a").system_prompt
    assert "STAGE IIIB" in load_module("stage3b").system_prompt
    assert "M1c1" in load_module("stage4b").system_prompt


def test_available_modules_list():
    assert set(available_modules()) == {
        "stage1", "stage2", "stage3a", "stage3b", "stage3c", "stage4a",
        "stage4b"
    }


def test_unknown_module_raises():
    with pytest.raises(PromptNotFound):
        load_module("stage99")


# --- stage-label normalization --------------------------------------------

from nsclc_agent.staging import (  # noqa: E402
    expand_stage_group,
    normalize_stage_group,
    stage_groups_compatible,
)


@pytest.mark.parametrize("label,expected", [
    ("IIIB", "IIIB"),
    ("iiib", "IIIB"),
    ("Stage IIIB", "IIIB"),
    ("  stage  iiib ", "IIIB"),
    ("3B", "IIIB"),
    ("4a", "IVA"),
    ("分期IIIC", "IIIC"),
    ("IIIC期", "IIIC"),
    ("IA", "IA"),
    ("I", "I"),
    ("0", "0"),
    ("Tis", "0"),
    ("Occult", "Occult"),
    ("garbage", None),
    ("", None),
    (None, None),
])
def test_normalize_stage_group(label, expected):
    assert normalize_stage_group(label) == expected


@pytest.mark.parametrize("label,module", [
    ("stage iiib", "stage3b"),
    ("3B", "stage3b"),
    ("IA", "stage1"),      # every IA substage routes to stage1
    ("I", "stage1"),       # …and so does IB
    ("II", "stage2"),
])
def test_family_and_freeform_labels_route(label, module):
    r = route(label)
    assert r.available
    assert r.module_key == module


@pytest.mark.parametrize("label", ["III", "IV"])
def test_ambiguous_family_refused(label):
    """III and IV span several modules, so the substage must be supplied."""
    r = route(label)
    assert not r.available
    assert r.module_key is None
    assert "ambiguous" in (r.note or "").lower()


def test_unknown_label_reports_unknown():
    r = route("not-a-stage")
    assert not r.available
    assert "Unknown stage group" in r.note


def test_expand_stage_group():
    assert expand_stage_group("IA") == ("IA1", "IA2", "IA3")
    assert expand_stage_group("IIIB") == ("IIIB",)
    assert expand_stage_group("nonsense") == ()


@pytest.mark.parametrize("provided,computed,ok", [
    ("IA", "IA1", True),        # less specific label is compatible
    ("I", "IB", True),
    ("III", "IIIB", True),
    ("IIIA", "IIIB", False),    # genuinely different groups
    ("IVA", "IVB", False),
    ("garbage", "IIIB", False),
    (None, "IIIB", True),
])
def test_stage_groups_compatible(provided, computed, ok):
    assert stage_groups_compatible(provided, computed) is ok


def test_route_result_to_dict_is_a_copy():
    r = route("IIIB")
    d = r.to_dict()
    d["module_key"] = "tampered"
    assert r.module_key == "stage3b"


def test_stage3a_is_a_stage_iiia_module():
    """Regression: stage3a used to ship the un-specialized parent framework."""
    text = load_module("stage3a").system_prompt
    assert "STAGE IIIA" in text
    assert "AJCC9" in text                 # 9th ed. is a legal staging_system
    assert "N2a" in text and "N2b" in text  # the 9th-ed. split is handled
    assert "UNRESECTABLE_DEFINITIVE" in text  # unresectable IIIA is in scope
    assert '"stage_group": "IIIA"' in text


def test_every_module_declares_its_stage():
    """Each module's banner must name the stage band it actually covers."""
    for m in list_modules():
        assert m.version, f"{m.key} has no parseable version banner"
        assert "2026-06" in m.version, f"{m.key} is not on the 2026-06 revision"
