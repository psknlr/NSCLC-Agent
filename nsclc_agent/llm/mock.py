"""Offline mock model: deterministic, tool-loop-capable, honest.

The mock is what keeps the *whole* agentic pipeline exercisable with no key
and no network — including the ReAct loop: it issues one real tool call, reads
the observation, and answers with schema-valid JSON that cites the evidence id
it actually received. It never fabricates clinical content beyond that
skeleton, and it cannot read images (its imaging answer says so).
"""

from __future__ import annotations

import json
from typing import Any

from .base import LLMResponse, ToolCall, ToolSpec


class MockLLMClient:
    name = "mock"
    model = "mock-agentic"
    available = True

    def __init__(self, *, vision: bool = False) -> None:
        self.supports_vision = vision

    # ------------------------------------------------------------------- chat
    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 1024,
        response_format_json: bool = False,
    ) -> LLMResponse:
        system = next((m.get("content") or "" for m in messages
                       if m.get("role") == "system"), "")
        system_text = system if isinstance(system, str) else json.dumps(system)
        user = next((m.get("content") for m in messages
                     if m.get("role") == "user"), "")

        if "IMAGING DESCRIPTOR EXTRACTION" in system_text:
            from ..perception.imaging import mock_findings_payload

            return self._reply(mock_findings_payload())

        if "CLINICAL DOCUMENT EXTRACTION" in system_text:
            return self._reply(json.dumps({
                "document_types": [],
                "histologic_category": None, "driver_mutations": {},
                "pd_l1": {}, "candidate_t": None, "candidate_n": None,
                "candidate_m": None, "specimen": None, "report_dates": [],
                "key_findings": [],
                "uncertainties": [
                    "Offline mock cannot read documents; configure a "
                    "vision-capable backend for real report reading."
                ],
            }, ensure_ascii=False))

        if "CHAT FACT EXTRACTION" in system_text:
            # Honest empty: the deterministic regex pass already extracted the
            # unambiguous facts; the mock must not invent any beyond them.
            return self._reply(json.dumps({"facts": {}}, ensure_ascii=False))

        if "问诊充分性审核者" in system_text or "病史充分性审核者" in system_text:
            return self._reply(json.dumps({
                "adequate": False, "missing_axes": [],
                "reason": "mock verifier defers to the rule-derived axis set",
                "contradictions": [],
            }, ensure_ascii=False))

        if "plan the task graph" in system_text:
            return self._reply(self._plan(user))

        tool_names = {t.name for t in (tools or [])}
        if "ask_case_question" in tool_names:
            return self._interview(user)

        if tools and not self._has_tool_observation(messages):
            # First agentic turn: gather one piece of real evidence.
            query = self._stage_hint(system_text) or "NSCLC"
            preferred = "trial_lookup" if "trial_lookup" in tool_names \
                else sorted(tool_names)[0]
            arguments = {"query": query} if preferred != "stage_lookup" \
                else {"t": "T1a", "n": "N0", "m": "M0"}
            return LLMResponse(
                text="", provider=self.name, model=self.model,
                finish_reason="tool_calls",
                tool_calls=[ToolCall(preferred, arguments, id="mock_call_1")],
            )

        evidence_ids = self._observed_evidence_ids(messages)
        if '"urgency"' in system_text:
            return self._reply(json.dumps({
                "urgency": "routine",
                "key_findings": ["mock speciality review — configure a real "
                                 "model for substantive panel opinions"],
                "concerns": [],
                "recommend_next": ["confirm staging adequacy at MDT"],
                "citations": evidence_ids,
            }, ensure_ascii=False))
        if '"intent"' in system_text:
            return self._reply(json.dumps({
                "intent": "curative",
                "summary": "Deterministic mock skeleton: the rule-mode plan in "
                           "this run is the substantive output; configure a "
                           "real backend for model-driven reasoning.",
                "options": [{"name": "See rule-mode plan",
                             "regimen_ids": [],
                             "rationale": "mock model adds no clinical content"}],
                "regimen_ids": [],
                "trial_refs": [],
                "uncertainties": ["mock output — no clinical judgment applied"],
                "citations": evidence_ids,
            }, ensure_ascii=False))
        return self._reply(json.dumps(
            {"note": "mock response", "citations": evidence_ids},
            ensure_ascii=False))

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _has_tool_observation(messages: list[dict[str, Any]]) -> bool:
        return any(m.get("role") == "tool" for m in messages)

    @staticmethod
    def _observed_evidence_ids(messages: list[dict[str, Any]]) -> list[str]:
        ids: list[str] = []
        for message in messages:
            if message.get("role") != "tool":
                continue
            try:
                payload = json.loads(message.get("content") or "{}")
            except json.JSONDecodeError:
                continue
            eid = payload.get("evidence_id")
            if isinstance(eid, str):
                ids.append(eid)
        return ids

    @staticmethod
    def _stage_hint(system_text: str) -> str:
        for line in system_text.splitlines():
            if line.startswith("Stage group:"):
                return line.split(":", 1)[1].split("(")[0].strip()
        return ""

    def _interview(self, user: Any) -> LLMResponse:
        try:
            payload = json.loads(user) if isinstance(user, str) else {}
        except json.JSONDecodeError:
            payload = {}
        probes = payload.get("suggested_probes") or {}
        questions = []
        for axis_id in (payload.get("required_open") or list(probes))[:4]:
            bank = probes.get(axis_id) or []
            if bank:
                questions.append({"axis_id": axis_id, "question": bank[0],
                                  "why": "mock: probe bank verbatim"})
        return LLMResponse(
            text="", provider=self.name, model=self.model,
            finish_reason="tool_calls",
            tool_calls=[ToolCall("ask_case_question",
                                 {"questions": questions,
                                  "interview_complete": not questions},
                                 id="mock_ask_1")],
        )

    def _plan(self, user: Any) -> str:
        try:
            context = json.loads(user) if isinstance(user, str) else {}
        except json.JSONDecodeError:
            context = {}
        tasks = [{"task_id": "T1", "agent": "InterviewAgent",
                  "objective": "close open axes", "depends_on": []}]
        prior = ["T1"]
        if context.get("has_images"):
            tasks.append({"task_id": "T2", "agent": "PerceptionAgent",
                          "objective": "read films", "depends_on": []})
            prior.append("T2")
        staging_id = f"T{len(tasks) + 1}"
        tasks.append({"task_id": staging_id, "agent": "StagingAgent",
                      "objective": "deterministic staging", "depends_on": prior})
        tasks.append({"task_id": f"T{len(tasks) + 1}", "agent": "TreatmentAgent",
                      "objective": "stage-routed reasoning",
                      "depends_on": [staging_id]})
        if context.get("panel_enabled"):
            tasks.append({"task_id": f"T{len(tasks) + 1}", "agent": "PanelAgent",
                          "objective": "MDT panel", "depends_on": [staging_id]})
        if context.get("dose_planning_allowed"):
            tasks.append({"task_id": f"T{len(tasks) + 1}", "agent": "DosePlanAgent",
                          "objective": "dose channel",
                          "depends_on": [tasks[-1]["task_id"]]})
        return json.dumps({"tasks": tasks}, ensure_ascii=False)

    def _reply(self, text: str) -> LLMResponse:
        return LLMResponse(
            text=text, provider=self.name, model=self.model,
            finish_reason="stop", prompt_tokens=0, completion_tokens=0,
        )
