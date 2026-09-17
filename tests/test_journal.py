"""The call journal: record/replay fidelity, divergence latch, authorisation."""

import pytest

from nsclc_agent.journal import (
    Journal, JournalDivergence, JournalEntry, request_hash,
)
from nsclc_agent.skills import SkillRegistry
from nsclc_agent.state import Budget
from nsclc_agent.tools import CapabilityBroker, ToolHealth, ToolRegistry


def test_record_and_replay_roundtrip(tmp_path):
    path = tmp_path / "run.jsonl"
    journal = Journal(path, mode="record")
    journal.write_meta({"llm_model": "test-model"})
    journal.record("tool", "trial_lookup", {"query": "PACIFIC"}, {"ok": True, "x": 1})
    journal.record("llm", "test-model", {"messages": []}, {"text": "hi"})

    loaded = Journal.load(path)
    assert loaded.meta["llm_model"] == "test-model"
    hit, result = loaded.next_result("tool", "trial_lookup", {"query": "PACIFIC"})
    assert hit and result == {"ok": True, "x": 1}
    hit, result = loaded.next_result("llm", "test-model", {"messages": []})
    assert hit and result["text"] == "hi"
    assert loaded.replayed == 2


def test_divergence_is_latched_and_raises(tmp_path):
    path = tmp_path / "run.jsonl"
    journal = Journal(path, mode="record")
    journal.record("tool", "trial_lookup", {"query": "PACIFIC"}, {"ok": True})
    loaded = Journal.load(path)
    with pytest.raises(JournalDivergence):
        loaded.next_result("tool", "trial_lookup", {"query": "LAURA"})
    assert loaded.diverged
    assert loaded.divergences[0]["differs_by"] == "arguments"


def test_exhaustion_is_not_divergence(tmp_path):
    path = tmp_path / "run.jsonl"
    Journal(path, mode="record").record("tool", "a", {}, {"ok": True})
    loaded = Journal.load(path)
    loaded.next_result("tool", "a", {})
    hit, _ = loaded.next_result("tool", "b", {})
    assert not hit
    assert not loaded.diverged
    assert loaded.live_after_exhaustion == 1


def test_sequence_gap_rejected(tmp_path):
    path = tmp_path / "bad.jsonl"
    entry = JournalEntry(seq=5, kind="tool", req_hash="x", result={})
    path.write_text(
        __import__("json").dumps(entry.to_dict()) + "\n", encoding="utf-8")
    with pytest.raises(Exception):
        Journal.load(path)


def test_replayed_tool_call_still_reauthorised(tmp_path):
    """A journal recorded as an oncologist grants nothing to a patient replay."""
    path = tmp_path / "run.jsonl"
    journal = Journal(path, mode="record")
    skills = SkillRegistry.discover()
    registry = ToolRegistry()

    onc = CapabilityBroker("oncologist", "routine", budget=Budget(),
                           skill_registry=skills,
                           active_skill="nsclc.dose_planning",
                           health=ToolHealth(), journal=journal)
    recorded = registry.call(onc, "regimen_detail",
                             regimen_id="osimertinib_adjuvant")
    assert recorded.ok

    replay = Journal.load(path)
    patient = CapabilityBroker("patient", "routine", budget=Budget(),
                               skill_registry=skills,
                               active_skill="nsclc.dose_planning",
                               health=ToolHealth(), journal=replay)
    denied = registry.call(patient, "regimen_detail",
                           regimen_id="osimertinib_adjuvant")
    # The broker denies before the journal is consulted: no dose payload.
    assert not denied.ok
    assert "regimen" not in denied.data
    assert replay.replayed == 0


def test_request_hash_stable_ordering():
    assert request_hash("tool", "x", {"a": 1, "b": 2}) == \
        request_hash("tool", "x", {"b": 2, "a": 1})
