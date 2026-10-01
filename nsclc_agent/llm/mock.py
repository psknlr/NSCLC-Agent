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
        if "submit_consult" in tool_names:
            return self._agent(messages)
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

    # ------------------------------------------------------------ agent mode
    def _agent(self, messages: list[dict[str, Any]]) -> LLMResponse:
        """Scripted agent-mode run: consult the governed pipeline, relay its
        opinion through submit_consult, accept any rule review. Exercises the
        real loop (tool call → observation → submit → review → resubmit)
        while saying plainly that no clinical reasoning happened."""
        starts = [i for i, m in enumerate(messages) if m.get("role") == "user"
                  and not str(m.get("content") or "").startswith("[系统]")]
        turn = messages[(starts[-1] + 1) if starts else 0:]
        names: dict[str, str] = {}
        seen: dict[str, Any] = {}
        for message in turn:
            for call in message.get("tool_calls") or []:
                names[call.get("id", "")] = (call.get("function") or {}).get("name", "")
            if message.get("role") == "tool":
                name = names.get(message.get("tool_call_id", ""), "")
                try:
                    seen[name] = json.loads(message.get("content") or "{}")
                except json.JSONDecodeError:
                    seen[name] = {}
        if "governed_reference" not in seen:
            return LLMResponse(
                text="先查看受治理流水线的参考意见。", provider=self.name,
                model=self.model, finish_reason="tool_calls",
                tool_calls=[ToolCall("governed_reference", {}, id="mock_gov_1")])
        reference = (seen["governed_reference"] or {}).get("data") or {}
        consult = self._mock_consult(reference)
        review = seen.get("submit_consult") or {}
        if review.get("status") == "review":
            consult["rule_responses"] = [
                {"rule_id": f.get("rule_id", ""), "decision": "accepted",
                 "reason": "离线 Mock：直接采纳规则引擎的意见"}
                for f in review.get("findings") or []]
        return LLMResponse(
            text="", provider=self.name, model=self.model,
            finish_reason="tool_calls",
            tool_calls=[ToolCall("submit_consult", consult,
                                 id=f"mock_submit_{len(turn)}")])

    @staticmethod
    def _mock_consult(reference: dict[str, Any]) -> dict[str, Any]:
        plan = reference.get("plan") or {}
        staging = reference.get("staging") or {}
        stage = staging.get("stage_group")
        options = [{"name": o.get("name") or "", "rationale": o.get("rationale") or "",
                    "regimen_ids": list(o.get("regimen_ids") or []),
                    "evidence": list(plan.get("trial_refs") or []) if i == 0 else [],
                    "preferred": i == 0}
                   for i, o in enumerate(plan.get("options") or [])]
        lines = ["> 离线 Mock 智能体：没有进行真实的模型推理。以下直接转述受治理流水线"
                 "的参考意见，用来演示工具调用与规则复核流程。接入真实模型后由模型主导会诊。",
                 "", f"**分期**：{stage or '未能分期'}"]
        emergency = reference.get("emergency_plan")
        if emergency:
            lines += ["", "**急症处置**："] + [f"- {a}" for a in
                                          emergency.get("immediate_actions") or []]
        if plan.get("summary"):
            lines += ["", str(plan["summary"])]
        if options:
            lines += ["", "**参考方案**："] + [
                f"{i + 1}. {o['name']}" + (f" — {o['rationale']}" if o["rationale"] else "")
                for i, o in enumerate(options)]
        questions = list(reference.get("open_questions") or [])
        if questions:
            lines += ["", "**还需要了解**："] + [f"- {q}" for q in questions]
        return {
            "reply": "\n".join(lines),
            "assessment": str(plan.get("summary") or ""),
            "stage_group": stage, "tnm": staging.get("tnm"),
            "intent": "emergency" if emergency else (plan.get("intent") or "undetermined"),
            "options": options,
            "workup": list(plan.get("workup_needed") or []),
            "questions": questions,
            "warnings": ["离线 Mock：未做真实临床推理"],
            "confidence": "low",
        }

    def _reply(self, text: str) -> LLMResponse:
        return LLMResponse(
            text=text, provider=self.name, model=self.model,
            finish_reason="stop", prompt_tokens=0, completion_tokens=0,
        )
