"""JSON bridge for the browser build (GitHub Pages).

The web app runs this package UNCHANGED inside the visitor's browser
(Pyodide in a Web Worker). Every capability the CLI offers is reached
through :func:`call` — one JSON string in, one JSON string out — so the
JavaScript side never touches Python objects and every safety property
(staging authority, rule engine, terminal critic, release gates, dose
channel) is the same code path as the command line.

API keys the visitor enters live only in this worker's memory for the
page session; they are sent to the provider the visitor chose and nowhere
else.
"""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from . import __version__
from .case import Case
from .llm.base import LLMError, NullLLMClient
from .render import render

#: Mutable page-session state (one visitor, one worker).
_STATE: dict[str, Any] = {
    "llm": None, "vision": None, "llm_config": {"provider": "none"},
    "session": None, "last_state": None, "last_role": "patient",
    #: "agent" (model-led) | "governed" (deterministic control plane).
    "mode": "governed", "agent": None,
    #: Agent runtime settings (hooks, specialists, memory, MCP, budgets);
    #: survive model changes and new consults.
    "agent_config": {},
}

_RELEASED = ("treatment_recommendation", "draft_for_tumor_board",
             "approved_by_tumor_board")


# ------------------------------------------------------------------ entry

def call(name: str, payload_json: str = "{}") -> str:
    """Dispatch one API call: ``{"ok": true, "result": …}`` or
    ``{"ok": false, "error": "…"}`` — never an exception across the bridge."""
    started = time.perf_counter()
    function = _API.get(name)
    if function is None:
        return json.dumps({"ok": False, "error": f"unknown API {name!r}"})
    try:
        payload = json.loads(payload_json or "{}")
        if not isinstance(payload, dict):
            raise ValueError("payload must be a JSON object")
        result = function(**payload)
        return json.dumps(
            {"ok": True, "result": result,
             "elapsed_ms": round((time.perf_counter() - started) * 1000)},
            ensure_ascii=False, default=str)
    except Exception as exc:  # noqa: BLE001 - the bridge never throws
        return json.dumps({"ok": False,
                           "error": f"{type(exc).__name__}: {exc}"},
                          ensure_ascii=False)


# ------------------------------------------------------------------ info

def info() -> dict[str, Any]:
    from .eval.run_eval import GOLDEN_DIR, _known_rule_ids
    from .knowledge import indications, regimens, trials
    from .platform_caps import IN_BROWSER
    from .safety import rules

    golden = json.loads(GOLDEN_DIR.joinpath("cases.json")
                        .read_text(encoding="utf-8"))["cases"]
    return {
        "version": __version__,
        "runtime": "browser (Pyodide/WebAssembly)" if IN_BROWSER
                   else "native Python",
        "counts": {
            "regimens": len(regimens.REGIMENS), "trials": len(trials.TRIALS),
            "indications": len(indications.INDICATIONS),
            "rules": len(rules.RULES),
            "golden_cases": len(golden),
            "audit_probes": sum(1 for c in golden if "audit_plan" in c),
        },
        "rule_ids": sorted(_known_rule_ids()),
        "providers": ["none", "mock", "poe", "minimax", "azure"],
        "llm": _describe_llms(),
    }


def examples() -> list[dict[str, Any]]:
    return EXAMPLES


def catalog() -> dict[str, Any]:
    """Regimen library and trial registry summaries — names, settings and
    anchors only; component dosing stays behind the dose channel."""
    from .knowledge import regimens, trials

    return {
        "regimens": {r.regimen_id: {"name": r.name, "setting": r.setting,
                                    "trial_ids": list(r.trial_ids),
                                    "contains_ici": r.contains_ici,
                                    "label_note": r.label_note}
                     for r in regimens.REGIMENS},
        "trials": {t.trial_id: {"name": t.name, "setting": t.setting,
                                "stage_groups": sorted(t.stage_groups),
                                "results": list(t.results),
                                "source": t.source}
                   for t in trials.TRIALS},
    }


# ------------------------------------------------------------------ LLM

def configure_llm(provider: str = "none", api_key: str = "", model: str = "",
                  base_url: str = "", region: str = "china",
                  group_id: str = "", endpoint: str = "",
                  api_version: str = "", vision: bool = True,
                  vision_model: str = "") -> dict[str, Any]:
    """Build the page's model clients from explicit settings (never from
    the environment — a browser has none)."""
    from .llm.mock import MockLLMClient
    from .llm.providers import (
        AZURE_DEFAULT_API_VERSION,
        MINIMAX_CHINA_BASE_URL,
        MINIMAX_GLOBAL_BASE_URL,
        POE_DEFAULT_BASE_URL,
        POE_DEFAULT_MODEL,
        POE_VISION_DEFAULT_MODEL,
        AzureOpenAIClient,
        MiniMaxClient,
        PoeClient,
    )

    provider = (provider or "none").strip().lower()
    api_key = (api_key or "").strip()
    llm: Any
    vision_client: Any = None
    if provider in ("none", ""):
        llm = NullLLMClient()
    elif provider == "mock":
        llm = MockLLMClient()
        vision_client = MockLLMClient(vision=True) if vision else None
    elif provider == "poe":
        if not api_key:
            raise LLMError("Poe 需要 API Key（poe.com/api/keys）")
        base = base_url.strip() or POE_DEFAULT_BASE_URL
        llm = PoeClient("poe", model.strip() or POE_DEFAULT_MODEL,
                        api_key=api_key, base_url=base)
        if vision:
            vision_client = PoeClient(
                "poe-vision", vision_model.strip() or POE_VISION_DEFAULT_MODEL,
                api_key=api_key, base_url=base, supports_vision=True)
    elif provider == "minimax":
        if not api_key:
            raise LLMError("MiniMax 需要 API Key")
        base = base_url.strip() or (
            MINIMAX_GLOBAL_BASE_URL if region == "global"
            else MINIMAX_CHINA_BASE_URL)
        llm = MiniMaxClient("minimax", model.strip() or "MiniMax-M3",
                            api_key=api_key, base_url=base,
                            group_id=group_id.strip() or None)
        if vision and vision_model.strip():
            vision_client = MiniMaxClient(
                "minimax-vision", vision_model.strip(), api_key=api_key,
                base_url=base, group_id=group_id.strip() or None,
                supports_vision=True)
    elif provider == "azure":
        if not (api_key and endpoint and model):
            raise LLMError("Azure 需要 API Key、Endpoint 与 Deployment")
        llm = AzureOpenAIClient(
            "azure", model.strip(), api_key=api_key, endpoint=endpoint.strip(),
            api_version=api_version.strip() or AZURE_DEFAULT_API_VERSION,
            supports_vision=bool(vision))
        vision_client = llm if vision else None
    else:
        raise LLMError(f"unknown provider {provider!r}")
    _STATE["llm"] = llm if getattr(llm, "available", False) else None
    _STATE["vision"] = vision_client
    _STATE["llm_config"] = {"provider": provider}
    _STATE["session"] = None  # a session is bound to its clients
    _STATE["agent"] = None
    # A connected model leads by default; without one only the governed
    # pipeline can run.
    _STATE["mode"] = "agent" if _STATE["llm"] else "governed"
    return _describe_llms()


def set_mode(mode: str) -> dict[str, Any]:
    """Switch between model-led agent mode and the governed pipeline."""
    mode = str(mode or "").strip().lower()
    if mode not in ("agent", "governed"):
        raise ValueError("mode must be 'agent' or 'governed'")
    if mode == "agent" and not _STATE["llm"]:
        raise LLMError("agent mode needs a configured model")
    _STATE["mode"] = mode
    return _describe_llms()


def ping_llm() -> dict[str, Any]:
    from .llm.providers import ping_client

    return ping_client(_STATE["llm"] or NullLLMClient())


def poe_catalog_check(model: str) -> dict[str, Any]:
    from .llm.providers import poe_model_check

    return poe_model_check(model)


def _describe_llms() -> dict[str, Any]:
    from .llm.providers import describe_client

    llm, vision = _STATE["llm"], _STATE["vision"]
    return {
        "llm": describe_client(llm) if llm else {"provider": "none",
                                                 "available": False},
        "vision": describe_client(vision) if vision else {"provider": "none"},
        "mode": _STATE["mode"],
    }


# ----------------------------------------------------------------- staging

def stage(t: str, n: str, m: str, prefix: str = "c") -> dict[str, Any]:
    from .staging import StagingError, route, stage_from_strings

    try:
        result = stage_from_strings(t, n, m, prefix=prefix)
    except StagingError as exc:
        return {"refused": True, "reason": str(exc)}
    payload = result.to_dict()
    payload["refused"] = False
    payload["module"] = route(result.stage_group).to_dict()
    return payload


# ------------------------------------------------------------ single run

def run_case(t: str | None = None, n: str | None = None,
             m: str | None = None, prefix: str = "c",
             presentation: str = "", question: str = "",
             facts: dict[str, Any] | None = None, role: str = "oncologist",
             allow_dose_planning: bool = False, enable_panel: bool = False,
             images: list[str] | None = None,
             reports: list[str] | None = None) -> dict[str, Any]:
    from .runner import NSCLCRunner

    case = Case(
        t=t or None, n=n or None, m=m or None, tnm_prefix=prefix or "c",
        presentation=presentation or "", question=question or "",
        facts=dict(facts or {}), images=list(images or []),
        reports=list(reports or []),
    )
    runner = NSCLCRunner(llm=_STATE["llm"], vision_llm=_STATE["vision"])
    state = runner.run_case(case, role=_role(role),
                            allow_dose_planning=bool(allow_dose_planning),
                            enable_panel=bool(enable_panel))
    _remember(state, _role(role))
    return _state_payload(state, _role(role))


def export_last() -> dict[str, Any]:
    """The full operator record of the last run (evidence payloads, trace,
    budget). A patient-role run exports only what its patient view showed —
    the record is clinician material, like every other non-patient view."""
    state = _STATE["last_state"]
    if state is None:
        return {}
    if _STATE["last_role"] == "patient":
        return {"release_status": state.release_status,
                "views": {"patient": render(state, "patient")}}
    return state.to_dict()


def _remember(state: Any, role: str) -> None:
    _STATE["last_state"] = state
    _STATE["last_role"] = role


# ------------------------------------------------------------ consultation

def chat_new(role: str = "oncologist",
             allow_dose_planning: bool = False) -> dict[str, Any]:
    from .conversation import ConsultationSession

    _STATE["session"] = ConsultationSession(
        llm=_STATE["llm"], vision_llm=_STATE["vision"], role=_role(role),
        allow_dose_planning=bool(allow_dose_planning))
    return {"role": _STATE["session"].role, "turns": 0}


def chat_turn(message: str = "", facts: dict[str, Any] | None = None,
              images: list[str] | None = None,
              reports: list[str] | None = None,
              enable_panel: bool = False) -> dict[str, Any]:
    session = _session()
    result = session.turn(message, facts=facts or None, images=images or None,
                          reports=reports or None,
                          enable_panel=bool(enable_panel))
    _remember(result.state, session.role)
    return _turn_payload(session, result)


def chat_whatif(description: str = "",
                facts: dict[str, Any] | None = None) -> dict[str, Any]:
    session = _session()
    result = session.what_if(description, facts=facts or None)
    return _turn_payload(session, result, what_if=True)


def chat_export() -> dict[str, Any]:
    return _session().to_dict()


def chat_import(data: dict[str, Any], role: str = "oncologist",
                allow_dose_planning: bool = False) -> dict[str, Any]:
    """Resume from an exported session. Authority (role, dose planning)
    comes from THIS call, never from the file — same contract as the CLI."""
    from .conversation import ConsultationSession

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "session.json"
        path.write_text(json.dumps(data, ensure_ascii=False),
                        encoding="utf-8")
        session = ConsultationSession.load(
            path, llm=_STATE["llm"], vision_llm=_STATE["vision"],
            role=_role(role), allow_dose_planning=bool(allow_dose_planning))
    _STATE["session"] = session
    return {"role": session.role, "turns": len(session.transcript),
            "facts": session.facts}


def _session() -> Any:
    if _STATE["session"] is None:
        chat_new()
    return _STATE["session"]


def _turn_payload(session: Any, result: Any, *,
                  what_if: bool = False) -> dict[str, Any]:
    payload = _state_payload(result.state, session.role)
    payload.update({
        "reply": result.reply, "plan_reused": result.plan_reused,
        "notes": list(result.notes),
        "extracted_facts": list(result.extracted_facts or []),
        "duration_s": round(result.duration_s, 3),
        "llm_calls": result.llm_calls, "what_if": what_if,
        "session_facts": {k: v for k, v in session.facts.items()
                          if not str(k).startswith("_")},
        "turns": len(session.transcript),
    })
    return payload


# ------------------------------------------------------------- agent mode

def agent_configure(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Update the agent runtime settings (partial update); applies to the
    running consult too."""
    from .agentic import AgentConfig

    merged = AgentConfig.from_dict({**_STATE["agent_config"],
                                    **dict(config or {})}).to_dict()
    _STATE["agent_config"] = merged
    if _STATE["agent"] is not None:
        _STATE["agent"].configure(merged)
    return merged


def agent_new(role: str = "oncologist",
              config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Start a model-led consultation (the model leads; the deterministic
    kernel is its toolbox and second opinion)."""
    from .agentic import AgentSession

    if config is not None:
        agent_configure(config)
    _STATE["agent"] = AgentSession(_STATE["llm"], role=_role(role),
                                   vision_llm=_STATE["vision"],
                                   config=_STATE["agent_config"], on_event=_emit)
    return {"role": _STATE["agent"].role, "turns": 0, "mode": "agent"}


def _agent() -> Any:
    if _STATE["agent"] is None:
        agent_new()
    return _STATE["agent"]


def agent_turn(message: str = "", facts: dict[str, Any] | None = None,
               images: list[str] | None = None,
               reports: list[str] | None = None) -> dict[str, Any]:
    session = _agent()
    result = session.turn(message, facts=facts or None, images=images or None,
                          reports=reports or None)
    payload = result.to_dict()
    payload.update({"role": session.role, "turns": len(session.turns),
                    "provider": getattr(_STATE["llm"], "name", "")})
    return payload


def agent_command(text: str) -> dict[str, Any]:
    """Expand a slash command. Prompt commands come back as the prompt to
    send (``kind: "prompt"``); local ones run here (``kind: "local"``)."""
    from .agentic.commands import expand

    out = expand(text)
    if out["kind"] != "local":
        return out
    if out["command"] == "clear":
        role = _STATE["agent"].role if _STATE["agent"] is not None else "oncologist"
        agent_new(role)
        return {"kind": "local", "command": "clear", "data": {},
                "text": "已开始新会诊（记忆与设置保留）。"}
    return {"kind": "local", **_agent().local_command(out["command"], out["arg"])}


def agent_commands() -> list[dict[str, str]]:
    from .agentic.commands import listing

    return listing()


def agent_rewind(turn: int | None = None) -> dict[str, Any]:
    if _STATE["agent"] is None:
        raise RuntimeError("no agent session")
    return _STATE["agent"].rewind(turn)


def agent_compact() -> dict[str, Any]:
    if _STATE["agent"] is None:
        raise RuntimeError("no agent session")
    return _STATE["agent"].compact()


def agent_info() -> dict[str, Any]:
    """The runtime as the agent sees it: tools, specialists, hooks, plan,
    usage, context, checkpoints (a catalog when no consult is running)."""
    from .agentic.session import runtime_catalog

    if _STATE["agent"] is not None:
        return _STATE["agent"].info()
    return runtime_catalog(_STATE["agent_config"])


def mcp_check(url: str, name: str = "server",
              headers: dict[str, str] | None = None) -> dict[str, Any]:
    """Connect to one MCP server and list its tools (settings 'test')."""
    from .agentic.mcp import connect

    _tools, status = connect([{"name": name, "url": url, "headers": headers or {}}])
    return status[0] if status else {"name": name, "ok": False, "error": "no server"}


def agent_export() -> dict[str, Any]:
    if _STATE["agent"] is None:
        raise RuntimeError("no agent session")
    return _STATE["agent"].to_dict()


def agent_import(data: dict[str, Any], role: str = "oncologist") -> dict[str, Any]:
    """Resume a model-led consultation; authority (role, settings) comes
    from this call and this page, never from the file."""
    from .agentic import AgentSession

    session = AgentSession.load(data, _STATE["llm"], role=_role(role),
                                vision_llm=_STATE["vision"],
                                config=_STATE["agent_config"], on_event=_emit)
    _STATE["agent"] = session
    return {"role": session.role, "turns": len(session.turns),
            "facts": session.facts, "mode": "agent", "plan": session.plan.items,
            "transcript": [{"message": t.get("message", ""), "reply": t.get("reply", ""),
                            "turn": i} for i, t in enumerate(session.turns)]}


def _emit(event: dict[str, Any]) -> None:
    """Stream an agent event to the page while the turn is still running
    (the worker forwards it; natively this is a no-op)."""
    from .platform_caps import IN_BROWSER

    if not IN_BROWSER:
        return
    try:
        import js  # type: ignore[import-not-found]

        js.agentEvent(json.dumps(event, ensure_ascii=False, default=str))
    except Exception:  # noqa: BLE001 - progress is best effort
        pass


# --------------------------------------------------------------- knowledge

def kg_info() -> dict[str, Any]:
    kg = _kg()
    return {**kg.describe(), "warning": kg.warning}


def kg_search(query: str = "", stage: str = "", gene: str = "",
              histology: str = "", topic: str = "", line: str = "",
              jurisdiction: str = "", direction: str = "", limit: int = 12,
              facts: dict[str, Any] | None = None) -> dict[str, Any]:
    kg = _kg()
    excluded: list[str] = []
    hits = kg.search(
        query, stage=stage or None, gene=gene or None,
        histology=histology or None, topic=topic or None, line=line or None,
        jurisdiction=jurisdiction or None, direction=direction or None,
        limit=max(1, min(int(limit or 12), 40)), case_facts=facts or None,
        case_stage=stage or None, excluded_verified_mismatch=excluded)
    return {"hits": hits, "excluded_verified_mismatch": excluded,
            "warning": kg.warning}


def kg_get(rec_id: str) -> dict[str, Any] | None:
    return _kg().get(rec_id)


def _kg() -> Any:
    from .knowledge.guideline_kg import load_default

    kg = load_default()
    if kg is None or not kg.available:
        raise RuntimeError("guideline KG store not available")
    return kg


# -------------------------------------------------------------- safety net

def audit_plan(staging: dict[str, Any] | None = None,
               facts: dict[str, Any] | None = None,
               plan: dict[str, Any] | None = None) -> dict[str, Any]:
    """Feed a crafted plan straight to the rule engine — the same call the
    audit-type golden probes make."""
    from .safety import rules

    violations = rules.check_plan(
        staging or {}, {"tnm": {}, **(facts or {})},
        {"regimen_ids": [], "options": [], **(plan or {})})
    rows = [v.to_dict() for v in violations]
    return {"violations": rows,
            "blocked": any(v["severity"] == "block" for v in rows)}


def golden_cases() -> list[dict[str, Any]]:
    from .eval.run_eval import GOLDEN_DIR

    cases = json.loads(GOLDEN_DIR.joinpath("cases.json")
                       .read_text(encoding="utf-8"))["cases"]
    return [{"id": c["id"], "kind": "audit" if "audit_plan" in c
             else "pipeline", "comment": c.get("_comment", ""),
             "expect": c.get("expect"), "case": c.get("case"),
             "audit_plan": c.get("audit_plan"),
             "audit_facts": c.get("audit_facts"),
             "audit_staging": c.get("audit_staging")} for c in cases]


def run_eval() -> dict[str, Any]:
    from .eval.run_eval import run_eval as _run

    report = _run()
    report.get("adjudication", {}).pop("per_case", None)
    return report


# ---------------------------------------------------------------- helpers

def _role(role: str | None) -> str:
    value = str(role or "").strip().lower()
    return value if value in ("patient", "oncologist", "researcher") \
        else "patient"


def _state_payload(state: Any, role: str) -> dict[str, Any]:
    from .state import NON_RELEASABLE_LEVELS

    audit = state.outputs.get("safety_audit") or {}
    views = {"patient": render(state, "patient")}
    payload: dict[str, Any] = {
        "release_status": state.release_status,
        "released": state.release_status in _RELEASED,
        "role": role,
        "views": views,
        "risk_mode": state.risk_mode,
    }
    if role == "patient":
        # A patient-role run exposes exactly what the patient view shows.
        return payload
    views["oncologist"] = render(state, "oncologist")
    views["researcher"] = render(state, "researcher")
    payload.update({
        "violations": audit.get("violations") or [],
        "issues": audit.get("issues") or [],
        "checks_run": audit.get("checks_run") or [],
        "claims": [{
            "claim_id": c.claim_id, "kind": c.kind, "text": c.text,
            "evidence_ids": list(c.evidence_ids),
            "support_relation": c.support_relation, "origin": c.origin,
        } for c in state.claims],
        "evidence": [{
            "evidence_id": eid, "level": e.level, "source": e.source,
            "summary": e.summary,
            "releasable": e.level not in NON_RELEASABLE_LEVELS,
        } for eid, e in state.evidence.items()],
        "indication_report": state.outputs.get("indication_report"),
        "run_meta": state.outputs.get("run_meta"),
        "tasks": [{"agent": t.agent, "status": t.status}
                  for t in state.tasks],
    })
    return payload


_API: dict[str, Callable[..., Any]] = {
    "info": info, "examples": examples, "catalog": catalog,
    "configure_llm": configure_llm, "ping_llm": ping_llm,
    "poe_catalog_check": poe_catalog_check,
    "stage": stage, "run_case": run_case, "export_last": export_last,
    "chat_new": chat_new, "chat_turn": chat_turn, "chat_whatif": chat_whatif,
    "chat_export": chat_export, "chat_import": chat_import,
    "set_mode": set_mode, "agent_new": agent_new, "agent_turn": agent_turn,
    "agent_export": agent_export, "agent_import": agent_import,
    "agent_configure": agent_configure, "agent_command": agent_command,
    "agent_commands": agent_commands, "agent_rewind": agent_rewind,
    "agent_compact": agent_compact, "agent_info": agent_info,
    "mcp_check": mcp_check,
    "kg_info": kg_info, "kg_search": kg_search, "kg_get": kg_get,
    "audit_plan": audit_plan, "golden_cases": golden_cases,
    "run_eval": run_eval,
}

_SCREEN = "No hemoptysis, no leg weakness, no fever."

#: One-click demonstrations — each exercises a distinct safety property.
EXAMPLES: list[dict[str, Any]] = [
    {"id": "iiib_egfr", "title": "IIIB 不可切除 · EGFR L858R",
     "subtitle": "cCRT + 奥希替尼巩固（LAURA），非度伐利尤单抗",
     "case": {"t": "T2b", "n": "N2b", "m": "M0",
              "presentation": "多站N2b，MDT判定不可切除。PET-CT+脑MRI确认M0。"
                              f"EGFR L858R。{_SCREEN}",
              "question": "根治性方案与巩固治疗？",
              "facts": {"driver_mutations": {"egfr": "L858R",
                                             "alk": "negative"},
                        "histologic_category": "adenocarcinoma",
                        "resectability_category": "UNRESECTABLE",
                        "ecog_ps": 1}}},
    {"id": "iv_pdl1_high", "title": "IVA · 驱动阴性 · PD-L1 80%",
     "subtitle": "帕博利珠单抗单药（KEYNOTE-024）+ 早期姑息整合",
     "case": {"t": "T2a", "n": "N0", "m": "M1b",
              "presentation": "肺腺癌，单发肾上腺转移，脑MRI阴性，NGS全阴性。"
                              f"ECOG 1。{_SCREEN}",
              "facts": {"driver_mutations": {"egfr": "negative",
                                             "alk": "negative"},
                        "histologic_category": "adenocarcinoma",
                        "pd_l1": {"tps": 80}, "ngs_done": True,
                        "ecog_ps": 1}}},
    {"id": "iv_exon20", "title": "IV · EGFR 20 外显子插入",
     "subtitle": "变异本体：amivantamab + 化疗（PAPILLON），拒绝奥希替尼",
     "case": {"t": "T2a", "n": "N0", "m": "M1b",
              "presentation": "肺腺癌，肾上腺转移，脑MRI阴性。NGS: EGFR exon 20 "
                              f"insertion。PD-L1 80%。{_SCREEN}",
              "facts": {"driver_mutations": {"egfr": "exon 20 insertion",
                                             "alk": "negative"},
                        "histologic_category": "adenocarcinoma",
                        "pd_l1": {"tps": 80}, "ngs_done": True,
                        "ecog_ps": 1}}},
    {"id": "post_osi", "title": "奥希替尼进展 · 后线序贯",
     "subtitle": "MARIPOSA-2 + KEYNOTE-789 教训；未问耐药机制则先补检",
     "case": {"t": "T2a", "n": "N0", "m": "M1c1",
              "presentation": "肺腺癌，一线奥希替尼后进展，进展期血浆NGS已完成。"
                              f"脑MRI阴性。{_SCREEN}",
              "facts": {"driver_mutations": {"egfr": "L858R",
                                             "alk": "negative"},
                        "histologic_category": "adenocarcinoma",
                        "ngs_done": True, "ecog_ps": 1,
                        "progression_ngs_done": True,
                        "treatment_history": [{"line": 1,
                                               "agents": ["osimertinib"],
                                               "status": "progression"}]}}},
    {"id": "cns_symptomatic", "title": "有症状未治脑转移",
     "subtitle": "CNS 定向局部治疗领跑；纯全身方案会被终审拦截",
     "case": {"t": "T3", "n": "N2b", "m": "M1c2",
              "presentation": "鳞癌，多发脑转移伴头痛，激素控制中。"
                              f"{_SCREEN}",
              "facts": {"driver_mutations": {"egfr": "negative",
                                             "alk": "negative"},
                        "histologic_category": "squamous",
                        "pd_l1": {"tps": 5}, "ngs_done": True, "ecog_ps": 1,
                        "cns_metastases": {"status": "present",
                                           "symptomatic": True,
                                           "treated": False,
                                           "burden": "extensive"}}}},
    {"id": "renal", "title": "肾功能不全 · CrCl 38",
     "subtitle": "器官闸门：培美曲塞骨架被响亮剔除，转 MDT/药师",
     "case": {"t": "T2a", "n": "N0", "m": "M1b",
              "presentation": f"肺腺癌，肾上腺转移，脑MRI阴性。{_SCREEN}",
              "facts": {"driver_mutations": {"egfr": "negative",
                                             "alk": "negative"},
                        "histologic_category": "adenocarcinoma",
                        "ngs_done": True, "ecog_ps": 1,
                        "organ_function": {"renal": {"crcl_ml_min": 38}}}}},
    {"id": "n3", "title": "IIIC · N3", "subtitle": "N3 不做手术：确定性放化疗 + 巩固",
     "case": {"t": "T2a", "n": "N3", "m": "M0",
              "presentation": "对侧纵隔淋巴结转移（N3），PET-CT与脑MRI M0。"
                              f"{_SCREEN}",
              "facts": {"driver_mutations": {"egfr": "negative",
                                             "alk": "negative"},
                        "histologic_category": "adenocarcinoma",
                        "resectability_category": "UNRESECTABLE",
                        "ecog_ps": 1}}},
    {"id": "emergency", "title": "急症 · 大咯血",
     "subtitle": "固定急症脚本短路，永不经模型改写",
     "case": {"presentation": "肺癌病史，突然大咯血不止，呼吸困难。"}},
]
