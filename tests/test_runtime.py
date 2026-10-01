"""The v1.0 agent runtime: tools, the shared loop, plan, specialist
sub-agents, hooks, memory and compaction, checkpoints, slash commands, MCP,
and the surfaces (web bridge, CLI)."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from nsclc_agent import webapi
from nsclc_agent.agentic import (
    AgentConfig, AgentDefinition, AgentSession, Cancelled, LoopResult, Plan, Tool,
    Toolset, builtin_hooks, estimate_tokens, expand, listing, load_definitions,
    load_instructions, repair_history, run_loop,
)
from nsclc_agent.agentic.hooks import HookRunner
from nsclc_agent.agentic.mcp import MCPClient, connect
from nsclc_agent.agentic.memory import summarize
from nsclc_agent.agentic.subagents import BUILTIN_AGENTS, SubAgentRunner
from nsclc_agent.agentic.toolbox import AgentToolbox
from nsclc_agent.llm.base import LLMError, LLMResponse, ToolCall
from nsclc_agent.llm.mock import MockLLMClient

ROOT = Path(__file__).resolve().parent.parent


class Scripted:
    """Replays a script of LLMResponses / callables(messages, tools)."""

    name = "scripted"
    model = "scripted-1"
    available = True
    supports_vision = False

    def __init__(self, *script):
        self.script = list(script)
        self.requests: list[dict] = []
        self.lock = threading.Lock()

    def chat(self, messages, *, tools=None, **kwargs):
        with self.lock:
            self.requests.append({"messages": json.loads(json.dumps(messages, default=str)),
                                  "tools": [t.name for t in tools or []]})
            if not self.script:
                raise LLMError("script exhausted")
            item = self.script.pop(0)
        return item(messages, [t.name for t in tools or []]) if callable(item) else item


def calls(*pairs, text=""):
    return LLMResponse(text=text, finish_reason="tool_calls", model="scripted-1",
                       tool_calls=[ToolCall(name, args, id=f"c{i}_{name}_{time.perf_counter_ns()}")
                                   for i, (name, args) in enumerate(pairs)])


def call(name, args=None, text=""):
    return calls((name, args or {}), text=text)


def say(text, finish="stop"):
    return LLMResponse(text=text, finish_reason=finish, model="scripted-1")


def valid_history(messages):
    pending = set()
    for message in messages:
        for tc in message.get("tool_calls") or []:
            pending.add(tc["id"])
        if message.get("role") == "tool":
            pending.discard(message["tool_call_id"])
    return not pending


def submit(reply="ok", **extra):
    return call("submit_consult", {"reply": reply, **extra})


# --------------------------------------------------------------------- tools

def test_tool_run_turns_failures_into_observations():
    def boom(**_):
        raise RuntimeError("kaput")

    assert Tool("t", "", {}, handler=lambda x: {"ok": True}).run({"y": 1})["summary"].startswith("bad arguments")
    assert Tool("t", "", {}, handler=boom).run({})["data"]["error"] == "RuntimeError: kaput"
    assert Tool("t", "", {}, handler=lambda: [1, 2]).run({}) == {"ok": True, "summary": "t done", "data": [1, 2]}
    assert Tool("t", "", {}).run({})["ok"] is False
    box = Toolset.of([Tool("a", "A. more", {}), Tool("b", "B", {})])
    assert box.only(["a"]).names() == ["a"] and box.without(["a"]).names() == ["b"]
    assert box.describe()[0]["description"] == "A"


def test_toolbox_tools_split_reader_and_writer():
    box = AgentToolbox()
    tools = {t.name: t for t in box.as_tools()}
    assert len(tools) == 20 and not tools["record_case_facts"].parallel_safe
    assert "record_case_facts" not in {t.name for t in box.as_tools(read_only=True)}


# ---------------------------------------------------------------------- loop

def _terminal(arguments):
    return {"status": "accepted"}, True


def test_loop_runs_read_only_calls_concurrently_and_keeps_call_order():
    gate = threading.Event()
    seen = {}

    def slow():
        seen["concurrent"] = gate.wait(timeout=2)
        return {"ok": True, "summary": "slow", "data": None}

    def fast():
        gate.set()
        return {"ok": True, "summary": "fast", "data": None}

    order = []
    toolset = Toolset.of([Tool("slow", "", {}, handler=slow), Tool("fast", "", {}, handler=fast),
                          Tool("write", "", {}, handler=lambda: order.append("w") or {"ok": True},
                               parallel_safe=False),
                          Tool("done", "", {}, terminal=True)])
    llm = Scripted(calls(("write", {}), ("slow", {}), ("fast", {}), ("done", {})))
    messages = [{"role": "user", "content": "x"}]
    result = run_loop(llm, messages, toolset, emit=lambda e: None, terminal=_terminal,
                      terminal_name="done", parallel=True)
    assert seen["concurrent"] is True
    assert result.stop == "terminal"
    assert [s["name"] for s in result.steps if s["kind"] == "tool"] == ["write", "slow", "fast"]
    assert valid_history(messages) and messages[-1]["role"] == "tool"


def test_loop_unknown_tool_nudge_truncation_and_limit():
    toolset = Toolset.of([Tool("done", "", {}, terminal=True)])
    llm = Scripted(call("nope"), say("长回答", finish="length"), say("纯文字"), say("还是文字"))
    messages = [{"role": "user", "content": "x"}]
    result = run_loop(llm, messages, toolset, emit=lambda e: None, terminal=_terminal,
                      terminal_name="done", nudge="请提交")
    assert result.stop == "text" and result.text == "还是文字"
    unknown = json.loads(messages[2]["content"])
    assert unknown["ok"] is False and "available" in unknown["data"]
    assert any("截断" in str(m.get("content")) for m in messages)
    assert any(m.get("content") == "[系统] 请提交" for m in messages)

    looping = Scripted(*[call("nope") for _ in range(3)])
    limited = run_loop(looping, [{"role": "user", "content": "x"}], toolset,
                       emit=lambda e: None, terminal=_terminal, terminal_name="done",
                       max_steps=3)
    assert limited.stop == "limit" and limited.steps[-1]["kind"] == "limit"


def test_loop_cancel_keeps_partial_trace_and_history_repairable():
    flag = {"stop": False}

    def stopper(**_):
        flag["stop"] = True
        return {"ok": True, "summary": "first", "data": None}

    toolset = Toolset.of([Tool("a", "", {}, handler=stopper, parallel_safe=False),
                          Tool("b", "", {}, parallel_safe=False), Tool("done", "", {}, terminal=True)])
    messages = [{"role": "user", "content": "x"}]
    partial = LoopResult()
    with pytest.raises(Cancelled):
        run_loop(Scripted(calls(("a", {}), ("b", {}))), messages, toolset, emit=lambda e: None,
                 terminal=_terminal, terminal_name="done", cancelled=lambda: flag["stop"],
                 result=partial)
    assert [s["name"] for s in partial.steps] == ["a"]
    assert not valid_history(messages)
    assert repair_history(messages, "interrupted") == 2 and valid_history(messages)


# ---------------------------------------------------------------------- plan

def test_plan_validates_and_reports():
    changes = []
    plan = Plan(on_change=changes.append)
    assert plan.update([])["ok"] is False
    out = plan.update([{"content": "分期", "status": "in_progress"},
                       {"content": "方案", "status": "weird"},
                       {"content": "  ", "status": "pending"}])
    assert out["ok"] and [i["status"] for i in plan.items] == ["in_progress", "pending"]
    assert out["_ui"]["plan"] == plan.items and changes
    two = plan.update([{"content": "a", "status": "in_progress"}, {"content": "b", "status": "in_progress"}])
    assert "one step" in two["data"]["note"]


def test_plan_lives_across_turns_and_is_shown_to_the_model():
    llm = Scripted(calls(("update_plan", {"items": [{"content": "核对分期", "status": "in_progress"}]}),
                         ("submit_consult", {"reply": "ok"})),
                   submit("ok2"))
    session = AgentSession(llm)
    first = session.turn("cT2aN0M1b")
    assert first.plan == [{"content": "核对分期", "status": "in_progress"}]
    session.turn("继续")
    assert "【当前会诊计划】" in llm.requests[-1]["messages"][-1]["content"]


# ----------------------------------------------------------------- subagents

def test_specialist_runs_its_own_loop_with_its_own_tools():
    def specialist(messages, tools):
        assert messages[0]["content"].startswith("你是 NSCLC-Agent 多学科会诊（MDT）中的临床药师")
        assert "【当前病例笔记】" in messages[1]["content"]
        assert "record_case_facts" not in tools and "delegate" not in tools
        assert "submit_consult" not in tools and "submit_report" in tools
        assert set(tools) - {"submit_report"} <= set(BUILTIN_AGENTS[5].tools)
        return call("interaction_check", {"medications": ["osimertinib", "rifampicin"]})

    llm = Scripted(
        call("delegate", {"agent": "pharmacy", "task": "核对相互作用"}),
        specialist,
        call("submit_report", {"report": "利福平为强 CYP3A4 诱导剂", "key_points": ["避免合用"],
                               "confidence": "high"}),
        submit("综合药师意见"),
    )
    events = []
    turn = AgentSession(llm, on_event=events.append).turn("奥希替尼联用利福平？")
    delegate = next(s for s in turn.steps if s.get("name") == "delegate")
    assert delegate["ok"] and delegate["report"]["confidence"] == "high"
    assert [c["name"] for c in delegate["children"] if c["kind"] == "tool"] == ["interaction_check"]
    assert turn.specialists == ["pharmacy"] and turn.llm_calls == 4
    lead_observation = json.loads(llm.requests[3]["messages"][-1]["content"])
    assert lead_observation["data"]["report"] == "利福平为强 CYP3A4 诱导剂"
    assert lead_observation["data"]["tools_used"] == ["interaction_check"]
    sub_events = [e for e in events if e.get("agent") == "pharmacy"]
    assert sub_events and all(e.get("depth") == 1 for e in sub_events
                              if e["type"] not in ("subagent_start", "subagent_end"))


def test_unknown_specialist_and_failed_specialist_are_observations():
    runner = SubAgentRunner(Scripted(), Toolset(), definitions=list(BUILTIN_AGENTS),
                            case_notes=dict, emit=lambda e: None)
    assert runner.delegate("dermatology", "x")["ok"] is False
    failed = runner.delegate("evidence", "x")  # the scripted model has nothing left
    assert failed["ok"] is False and "failed" in failed["summary"]


def test_custom_and_disabled_specialists(tmp_path):
    (tmp_path / "geri.md").write_text(
        "---\nname: Geriatric Oncology\ntitle: 老年肿瘤科医师\ndescription: 老年综合评估\n"
        "tools: prognosis, check_organ_function\n---\n关注耐受性。\n", encoding="utf-8")
    (tmp_path / "broken.md").write_text("no front matter", encoding="utf-8")
    custom = load_definitions(tmp_path)
    assert [d.name for d in custom] == ["geriatric_oncology"] and not custom[0].builtin
    session = AgentSession(Scripted(), config={"custom_agents": [custom[0].to_dict()],
                                               "disabled_agents": ["pharmacy"]})
    names = [d.name for d in session.definitions]
    assert "geriatric_oncology" in names and "pharmacy" not in names
    assert "老年肿瘤科医师" in session._system()["content"]
    with pytest.raises(ValueError):
        AgentDefinition.from_dict({"title": "x"})
    off = AgentSession(Scripted(), config={"subagents": False})
    assert "delegate" not in off._toolset()[0].names()


# --------------------------------------------------------------------- hooks

def _review(reply, *, role="oncologist", hooks=None, before=(), **consult):
    llm = Scripted(*before, submit(reply, **consult))
    session = AgentSession(llm, role=role, config={"max_review_rounds": 0, "hooks": hooks or {}})
    return session.turn("问题"), session


def test_citation_provenance_flags_unresolvable_references_only():
    turn, _ = _review("方案", options=[{"name": "x", "evidence": ["FAKE-TRIAL-999", "KEYNOTE024"]}])
    finding = next(f for f in turn.review["findings"] if f["rule_id"] == "UNVERIFIED_CITATION")
    assert "FAKE-TRIAL-999" in finding["message"] and "KEYNOTE024" not in finding["message"]
    assert finding["hook"] == "citation_provenance"
    clean, _ = _review("方案", options=[{"name": "x", "evidence": ["FLAURA"]}],
                       before=[call("search_trials", {"query": "FLAURA"})])
    assert not any(f["rule_id"] == "UNVERIFIED_CITATION" for f in clean.review["findings"])


def test_dose_provenance_and_patient_role():
    turn, _ = _review("奥希替尼 80 mg 每日一次", role="patient")
    ids = {f["rule_id"] for f in turn.review["findings"]}
    assert {"DOSE_NOT_FROM_LIBRARY", "DOSE_TO_PATIENT"} <= ids
    looked_up, session = _review("奥希替尼 80 mg 每日一次",
                                 before=[call("regimen_dosing", {"regimen_id": "osimertinib_first_line"})])
    assert not any(f["rule_id"] == "DOSE_NOT_FROM_LIBRARY" for f in looked_up.review["findings"])
    assert "osimertinib_first_line" in session.state["dosing_seen"]


def test_emergency_hooks_and_disabling_them():
    llm = Scripted(submit("建议化疗"))
    events = []
    session = AgentSession(llm, config={"max_review_rounds": 0}, on_event=events.append)
    turn = session.turn("肺癌病史，突然大咯血不止，呼吸困难。")
    assert any(e["type"] == "alert" for e in events)
    assert any(f["rule_id"] == "EMERGENCY_NOT_ADDRESSED" and f["severity"] == "block"
               for f in turn.review["findings"])
    assert turn.reply == "建议化疗"  # advisory: nothing is blocked or rewritten
    quiet, _ = _review("建议化疗", hooks={"emergency_screen": False, "emergency_addressed": False})
    assert not quiet.review["findings"] and quiet.emergency is None


def test_broken_hook_is_reported_not_fatal():
    def broken(ctx):
        raise KeyError("x")

    from nsclc_agent.agentic.hooks import Hook

    runner = HookRunner([Hook("broken", "stop", "坏", "", broken)])
    (hook, result), = runner.run("stop", {})
    assert result.findings[0]["rule_id"] == "HOOK_ERROR:broken"
    assert {h.name for h in builtin_hooks()} >= {"rule_review", "citation_provenance",
                                                  "dose_provenance", "evidence_ledger"}


# -------------------------------------------------------------------- memory

def test_token_estimate_and_instruction_files(tmp_path):
    assert estimate_tokens("肺癌") == 2 and estimate_tokens("abcdefgh") == 2
    home, cwd = tmp_path / "home", tmp_path / "proj"
    (home / ".nsclc-agent").mkdir(parents=True)
    (cwd / ".nsclc-agent").mkdir(parents=True)
    (home / ".nsclc-agent" / "NSCLC.md").write_text("用户偏好：先结论", encoding="utf-8")
    (cwd / "NSCLC.md").write_text("本院：CSCO 优先", encoding="utf-8")
    text = load_instructions(cwd, home)
    assert text.index("先结论") < text.index("CSCO 优先")


def test_memory_reaches_prompts_and_proposals_reach_the_clinician():
    llm = Scripted(calls(("remember", {"note": "本院无奥希替尼", "scope": "formulary"}),
                         ("submit_consult", {"reply": "ok"})))
    turn = AgentSession(llm, config={"instructions": "- 先给结论"}).turn("x")
    assert "先给结论" in llm.requests[0]["messages"][0]["content"]
    assert turn.memory == ["本院无奥希替尼"]


def test_summarize_falls_back_when_the_model_fails():
    class Down(Scripted):
        def chat(self, *a, **k):
            raise LLMError("down")

    text, by_model = summarize(Down(), [{"message": "m", "reply": "r"}])
    assert not by_model and text.startswith("【此前会诊摘要】")
    text, by_model = summarize(MockLLMClient(), [{"message": "病例" * 20, "reply": "结论" * 20}])
    assert by_model and "离线 Mock" in text


def test_manual_compaction_drops_checkpoints():
    session = AgentSession(MockLLMClient())
    for text in ("cT2aN0M1b 肺腺癌", "补充：ALK 阴性", "补充：PS 1"):
        session.turn(text)
    assert len(session.checkpoints) == 3
    out = session.compact()
    assert out["ok"] and out["after"] < out["before"] and session.checkpoints == []
    with pytest.raises(ValueError):
        session.rewind()
    assert session.compact()["ok"] is False


# --------------------------------------------------------------- checkpoints

def test_rewind_restores_notes_plan_history_and_ledger():
    llm = Scripted(
        submit("第一轮"),
        calls(("record_case_facts", {"facts": {"ecog_ps": 3}}),
              ("update_plan", {"items": [{"content": "重新评估", "status": "in_progress"}]}),
              ("search_trials", {"query": "FLAURA"}),
              ("submit_consult", {"reply": "第二轮"})),
    )
    session = AgentSession(llm)
    session.turn("cT2aN0M1b，ECOG 1")
    before = (json.dumps(session.facts, sort_keys=True), len(session.messages),
              set(session.state["evidence_seen"]))
    session.turn("PS 变差")
    assert session.facts["ecog_ps"] == 3 and session.plan.items and "FLAURA" in session.state["evidence_seen"]
    out = session.local_command("rewind", "2")
    assert out["data"]["message"] == "PS 变差" and len(session.turns) == 1
    assert (json.dumps(session.facts, sort_keys=True), len(session.messages),
            set(session.state["evidence_seen"])) == before
    assert session.plan.items == [] and valid_history(session.messages)
    with pytest.raises(ValueError):
        session.rewind(5)


def test_cancel_stops_the_turn_and_keeps_history_valid():
    session = None

    def cancel_now(messages, tools):
        session.cancel()
        return calls(("search_trials", {"query": "FLAURA"}), ("prognosis", {}))

    session = AgentSession(Scripted(cancel_now), config={"parallel": False})
    turn = session.turn("x")
    assert turn.stop == "cancelled" and "中断" in turn.reply
    assert valid_history(session.messages)
    assert len(session.turns) == 1  # a cancelled turn still occupies its checkpoint slot


# ---------------------------------------------------------- config & session

def test_config_is_clamped_and_legacy_arguments_still_work():
    cfg = AgentConfig.from_dict({"max_steps": 999, "temperature": "hot", "hooks": {"x": 0},
                                 "mcp_servers": [{"name": "a"}, {"url": "http://x"}]})
    assert cfg.max_steps == 64 and cfg.temperature == 0.2 and cfg.hooks == {"x": False}
    assert cfg.mcp_servers == [{"url": "http://x"}]
    session = AgentSession(Scripted(), max_steps=3, config={"max_steps": 10})
    assert session.config.max_steps == 3
    with pytest.raises(TypeError):
        AgentSession(Scripted(), bogus=1)
    session.configure({"hooks": {"rule_review": False}})
    assert not session.hooks.enabled["rule_review"] and session.config.max_steps == 3


def test_session_v2_roundtrip_keeps_plan_ledger_and_checkpoints():
    session = AgentSession(MockLLMClient(), role="oncologist")
    session.turn("cT2aN0M1b 肺腺癌，请 MDT")
    session.turn("补充：ALK 阴性")
    data = json.loads(json.dumps(session.to_dict(), ensure_ascii=False))
    assert data["format"] == "nsclc-agent-session/2" and len(data["checkpoints"]) == 2
    data["checkpoints"][0]["facts"] = {"release_status": "approved", "ecog_ps": 1}
    resumed = AgentSession.load(data, MockLLMClient(), role="patient")
    assert resumed.role == "patient" and resumed.plan.items == session.plan.items
    assert resumed.state["evidence_seen"] == session.state["evidence_seen"]
    resumed.rewind(0)
    assert "release_status" not in resumed.facts and resumed.turns == []
    data["checkpoints"][0]["messages"] = 10**6
    assert AgentSession.load(data, MockLLMClient()).checkpoints == []
    legacy = {"format": "nsclc-agent-session/1", "facts": {"ecog_ps": 1}, "narrative": ["x"],
              "turns": [{"message": "x", "reply": "y"}],
              "messages": [{"role": "user", "content": "x"},
                           {"role": "assistant", "content": "", "tool_calls": [
                               {"id": "t1", "type": "function",
                                "function": {"name": "prognosis", "arguments": "{}"}}]}]}
    old = AgentSession.load(legacy, MockLLMClient())
    assert old.facts["ecog_ps"] == 1 and valid_history(old.messages)


# ------------------------------------------------------------------ commands

def test_commands_expand_and_local_commands_answer():
    assert expand("hello") == {"kind": "text", "text": "hello"}
    assert expand("/nope")["kind"] == "unknown"
    mdt = expand("/MDT 关注寡转移")
    assert mdt["kind"] == "prompt" and "delegate" in mdt["prompt"] and mdt["prompt"].endswith("关注寡转移")
    assert expand("/rewind 2") == {"kind": "local", "command": "rewind", "arg": "2"}
    assert len(listing()) == 15
    session = AgentSession(MockLLMClient())
    session.turn("cT2aN0M1b")
    for name in ("help", "usage", "agents", "tools", "hooks", "memory"):
        out = session.local_command(name)
        assert out["command"] == name and out["text"]
    assert "/mdt" in session.local_command("help")["text"]
    with pytest.raises(ValueError):
        session.local_command("mdt")


# ----------------------------------------------------------------------- MCP

class _FakeMCP(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 - http.server API
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.seen.append((body, self.headers.get("Mcp-Session-Id")))
        if "id" not in body:
            self.send_response(202)
            self.end_headers()
            return
        method = body["method"]
        if method == "initialize":
            result = {"protocolVersion": "2025-06-18", "serverInfo": {"name": "fake-formulary"},
                      "capabilities": {"tools": {}}}
        elif method == "tools/list":
            result = {"tools": [{"name": "formulary.lookup", "description": "Local formulary",
                                 "inputSchema": {"type": "object",
                                                 "properties": {"drug": {"type": "string"}},
                                                 "required": ["drug"]}}]}
        elif method == "tools/call":
            drug = body["params"]["arguments"].get("drug")
            result = {"content": [{"type": "text", "text": f"{drug}: not on formulary"}],
                      "structuredContent": {"drug": drug, "listed": False}}
        else:
            result = None
        payload = {"jsonrpc": "2.0", "id": body["id"]}
        payload.update({"result": result} if result is not None else
                       {"error": {"code": -32601, "message": "no such method"}})
        if self.server.sse:
            data, ctype = f"event: message\ndata: {json.dumps(payload)}\n\n", "text/event-stream"
        else:
            data, ctype = json.dumps(payload), "application/json"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Mcp-Session-Id", "sess-42")
        self.end_headers()
        self.wfile.write(data.encode())

    def log_message(self, *args):
        pass


@pytest.fixture(params=[False, True], ids=["json", "sse"])
def mcp_server(request):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeMCP)
    server.seen, server.sse = [], request.param
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, f"http://127.0.0.1:{server.server_address[1]}/mcp"
    server.shutdown()


def test_mcp_client_discovers_and_calls_tools(mcp_server):
    server, url = mcp_server
    tools, status = connect([{"name": "formulary", "url": url},
                             {"name": "off", "url": url, "enabled": False}])
    assert status == [{"name": "formulary", "ok": True, "tools": ["mcp__formulary__formulary_lookup"],
                       "server": {"name": "fake-formulary"}}]
    out = tools[0].run({"drug": "osimertinib"})
    assert out["ok"] and out["data"]["structured"]["listed"] is False
    assert server.seen[-1][1] == "sess-42"  # the session id is sent back
    with pytest.raises(Exception):
        MCPClient("x", url)._rpc("bogus/method")


def test_mcp_tools_join_the_agent_toolbox(mcp_server):
    _server, url = mcp_server
    llm = Scripted(call("mcp__formulary__formulary_lookup", {"drug": "osimertinib"}),
                   submit("本院目录无奥希替尼"))
    events = []
    session = AgentSession(llm, config={"mcp_servers": [{"name": "formulary", "url": url}]},
                           on_event=events.append)
    turn = session.turn("目录里有奥希替尼吗？")
    assert "mcp__formulary__formulary_lookup" in llm.requests[0]["tools"]
    assert "mcp__formulary__formulary_lookup" in llm.requests[0]["messages"][0]["content"]
    assert turn.steps[0]["ok"] and "not on formulary" in turn.steps[0]["summary"]
    assert any(e["type"] == "mcp" for e in events)
    bad, status = connect([{"name": "down", "url": "http://127.0.0.1:9/mcp"}], timeout=2)
    assert bad == [] and status[0]["ok"] is False


# ------------------------------------------------------------------ surfaces

def _api(name, **payload):
    out = json.loads(webapi.call(name, json.dumps(payload)))
    assert out["ok"], out.get("error")
    return out["result"]


@pytest.fixture
def mock_llm():
    webapi.configure_llm("mock")
    yield
    webapi._STATE["agent_config"] = {}
    webapi.configure_llm("none")


def test_webapi_runtime_apis(mock_llm):
    catalog = _api("agent_info")
    assert len(catalog["builtin_agents"]) == 7 and len(catalog["commands"]) == 15
    cfg = _api("agent_configure", config={"hooks": {"fact_seed": False}, "max_steps": 6})
    assert cfg["max_steps"] == 6 and cfg["hooks"] == {"fact_seed": False}
    _api("agent_new", role="oncologist")
    expanded = _api("agent_command", text="/mdt")
    assert expanded["kind"] == "prompt"
    turn = _api("agent_turn", message=expanded["prompt"] + " cT2aN0M1b 肺腺癌")
    assert turn["specialists"] and turn["plan"] and turn["context"]["tokens"] > 0
    assert not any(s["kind"] == "hook" and s["name"] == "fact_seed" for s in turn["steps"])
    assert "模型调用" in _api("agent_command", text="/usage")["text"]
    assert _api("agent_command", text="/nope")["kind"] == "unknown"
    info = _api("agent_info")
    assert info["turns"] == 1 and info["checkpoints"][0]["turn"] == 0
    assert _api("agent_rewind")["turns"] == 0
    assert _api("agent_compact")["ok"] is False
    assert _api("agent_command", text="/clear")["command"] == "clear"
    assert _api("mcp_check", url="http://127.0.0.1:9/mcp")["ok"] is False
    exported = _api("agent_export")
    resumed = _api("agent_import", data=exported, role="patient")
    assert resumed["role"] == "patient" and resumed["transcript"] == []


def _cli(*args, timeout=120):
    return subprocess.run([sys.executable, "-m", "nsclc_agent", "agent", "--llm-provider", "mock",
                           *args], cwd=ROOT, capture_output=True, text=True, timeout=timeout)


def test_cli_stream_json_commands_and_flags(tmp_path):
    proc = _cli("--output-format", "stream-json", "--message", "cT2aN0M1b 肺腺癌，请 MDT",
                "--message", "/usage", "--no-memory")
    assert proc.returncode == 0, proc.stderr
    lines = [json.loads(line) for line in proc.stdout.splitlines()]
    types = [line["type"] for line in lines]
    assert types[0] == "turn_start" and "subagent_start" in types and "plan" in types
    result = next(line for line in lines if line["type"] == "result")
    assert result["stop"] == "terminal" and result["specialists"]
    assert lines[-1]["type"] == "command" and lines[-1]["command"] == "usage"
    bad = _cli("--no-hook", "nope", "--message", "x")
    assert bad.returncode == 2 and "unknown hook" in bad.stderr
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "nutrition.md").write_text("---\nname: nutrition\ntitle: 营养科医师\n"
                                         "description: 营养评估\ntools: prognosis\n---\n关注营养。\n",
                                         encoding="utf-8")
    listed = _cli("--agents-dir", str(agents), "--disable-agent", "pharmacy", "--message", "/agents",
                  "--quiet")
    assert "营养科医师" in listed.stdout and "临床药师" not in listed.stdout
    unknown = _cli("--message", "/nope")
    assert unknown.returncode == 1 and "/help" in unknown.stderr
