"""Bilingual surfaces: the shared display dictionary, English agent output,
English hooks / commands / CLI, and the emergency screen on English input."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from nsclc_agent import webapi
from nsclc_agent.agentic import AgentSession
from nsclc_agent.agentic.commands import expand, listing
from nsclc_agent.agentic.prompts import LANGUAGE_DIRECTIVE_EN
from nsclc_agent.i18n import HAN_RE, _table, english_half, localize, translate
from nsclc_agent.llm.base import LLMResponse, ToolCall
from nsclc_agent.llm.mock import MockLLMClient

ROOT = Path(__file__).resolve().parent.parent


def test_dictionary_is_complete_and_compiles():
    data = json.loads((ROOT / "nsclc_agent/i18n/en.json").read_text(encoding="utf-8"))
    assert len(data["phrases"]) > 900 and data["rules"]
    for value in data["phrases"].values():
        assert not HAN_RE.search(value), value
    for source, _ in data["rules"]:
        re.compile(source)
    phrases, phrase_re, rules = _table("en")
    assert phrase_re is not None and len(rules) == len(data["rules"])


@pytest.mark.parametrize("zh,en", [
    ("新建会诊", "New consult"),
    ("1 次工具调用", "1 tool call"),
    ("5 次工具调用", "5 tool calls"),
    ("第 3 步", "step 3"),
    ("分期：IIIB（AJCC/UICC 9th edition，确定性引擎计算）",
     "Stage: IIIB (AJCC/UICC 9th edition, computed by the deterministic engine)"),
    ("大咯血 / massive hemoptysis", "Massive hemoptysis"),
])
def test_translate(zh, en):
    assert translate(zh) == en


def test_translate_leaves_chinese_mode_and_non_strings_alone():
    assert translate("新建会诊", "zh") == "新建会诊"
    assert translate(3) == 3
    assert localize({"a": ["新建会诊", 1]}) == {"a": ["New consult", 1]}
    assert english_half("急诊：气道保护；ED now: airway protection") == "ED now: airway protection"


def test_every_web_interview_question_has_english():
    from nsclc_agent.interview.axes import AXES

    axes = AXES.values() if isinstance(AXES, dict) else AXES
    for axis in axes:
        for probe in axis.probes:
            assert not HAN_RE.search(translate(probe)), probe


# ------------------------------------------------------------------ agent

def test_english_session_instructs_the_model_and_localises_hooks():
    seen = []

    class Recorder:
        name, model, available, supports_vision = "rec", "rec-1", True, False

        def chat(self, messages, **kw):
            seen.append(messages[0]["content"])
            return LLMResponse(text="", finish_reason="tool_calls", tool_calls=[
                ToolCall("submit_consult", {"reply": "Give 80 mg daily.",
                                            "options": [{"name": "x", "evidence": ["FAKE-1"]}]},
                         id="s")])

    turn = AgentSession(Recorder(), role="patient",
                        config={"language": "en", "max_review_rounds": 0}).turn(
        "History of lung cancer; sudden massive hemoptysis that will not stop")
    assert seen[0].endswith(LANGUAGE_DIRECTIVE_EN)
    messages = {f["rule_id"]: f["message"] for f in turn.review["findings"]}
    assert "will set the exact dose" in messages["DOSE_TO_PATIENT"]
    assert "registry" in messages["UNVERIFIED_CITATION"]
    assert "emergency first" in messages["EMERGENCY_NOT_ADDRESSED"]
    assert not any(HAN_RE.search(m) for m in messages.values())
    hook_text = [s["text"] for s in turn.steps if s["kind"] == "hook"]
    assert hook_text and all(not HAN_RE.search(t) for t in hook_text)


def test_english_mock_consult_mdt_and_commands():
    session = AgentSession(MockLLMClient(), config={"language": "en"})
    turn = session.turn(expand("/mdt cT2aN0M1b adenocarcinoma, EGFR negative", "en")["prompt"])
    assert not HAN_RE.search(turn.reply)
    assert [i["content"] for i in turn.plan][0] == "Understand the case and check the stage"
    titles = [s["title"] for s in turn.steps if s.get("name") == "delegate"]
    assert titles == ["Radiologist", "Medical oncologist"]
    for name in ("help", "usage", "agents", "hooks", "memory", "tools"):
        assert not HAN_RE.search(session.local_command(name)["text"]), name
    assert not any(HAN_RE.search(c["description"]) for c in listing("en"))
    assert expand("/nope", "en")["message"].startswith("Unknown command")


def test_webapi_language_switch():
    webapi.configure_llm("mock")
    try:
        assert json.loads(webapi.call("set_language", json.dumps({"lang": "en"})))["ok"]
        assert json.loads(webapi.call("set_language", json.dumps({"lang": "fr"})))["ok"] is False
        info = json.loads(webapi.call("agent_info", "{}"))["result"]
        assert info["config"]["language"] == "en"
        assert info["hooks"][0]["title"] == "Emergency screen"
        examples = json.loads(webapi.call("examples", "{}"))["result"]
        assert all(e["en"]["title"] and e["en"]["presentation"] for e in examples)
    finally:
        webapi.set_language("zh")
        webapi.configure_llm("none")


def test_english_examples_still_screen_emergencies():
    from nsclc_agent.safety.emergencies import screen

    emergency = next(e for e in webapi.EXAMPLES if e["id"] == "emergency")
    text = webapi.EXAMPLES_EN["emergency"]["presentation"]
    assert screen(emergency["case"]["presentation"]).hard_hits
    assert [h["signal_id"] for h in screen(text).hard_hits] == ["massive_hemoptysis"]


def test_cli_english():
    proc = subprocess.run(
        [sys.executable, "-m", "nsclc_agent", "agent", "--llm-provider", "mock", "--lang", "en",
         "--message", "cT2aN0M1b lung adenocarcinoma, EGFR negative, ALK negative. Please convene an MDT.",
         "--message", "/usage"], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert "Offline mock agent" in proc.stdout and "model calls" in proc.stdout
    assert "consulting Radiologist" in proc.stderr and "hooks review" in proc.stderr


def test_every_web_ui_literal_has_english():
    """Every Chinese string literal in the web app translates fully, apart
    from the switch back to Chinese (shown in Chinese on purpose)."""
    keep_chinese = {"中文界面 · 智能体将用中文回答", "切换到中文", "中文"}
    source = (ROOT / "web/assets/app.js").read_text(encoding="utf-8")
    literals = {m.group(1) for m in re.finditer(r'"((?:[^"\\\n]|\\.)*)"', source)
                if HAN_RE.search(m.group(1))}
    missing = sorted(t for t in literals - keep_chinese if HAN_RE.search(translate(t)))
    assert missing == [], missing


def test_v14_display_strings_translate():
    from nsclc_agent.conversation import ConsultationSession
    from tests.test_case_kras_later_line import CASE

    reply = ConsultationSession(role="oncologist").turn(CASE).reply
    shown = [line for line in reply.split("\n")
             if line.startswith(("生物标志物分类", "转移部位", "＋ 支持治疗"))]
    assert len(shown) >= 4
    for line in shown:
        assert not HAN_RE.search(translate(line)), line
