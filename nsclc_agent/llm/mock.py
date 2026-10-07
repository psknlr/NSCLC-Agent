"""Offline mock model: deterministic, tool-loop-capable, honest.

The mock is what keeps the *whole* agentic pipeline exercisable with no key
and no network — including the ReAct loop: it issues one real tool call, reads
the observation, and answers with schema-valid JSON that cites the evidence id
it actually received. It never fabricates clinical content beyond that
skeleton, and it cannot read images (its imaging answer says so).
"""

from __future__ import annotations

import json
import re
from typing import Any

from .base import LLMResponse, ToolCall, ToolSpec


def _intent(value: Any) -> str:
    """A governed plan's intent as a consult intent ("workup" is not one)."""
    value = str(value or "")
    return value if value in ("curative", "palliative", "supportive") else "undetermined"


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

        if "会诊记录压缩器" in system_text:
            # Honest compaction: an excerpt, labelled as such.
            excerpt = str(user or "")[-1500:]
            return self._reply("- 离线 Mock：以下为逐轮摘录，未做模型压缩\n" + excerpt)

        if "plan the task graph" in system_text:
            return self._reply(self._plan(user))

        tool_names = {t.name for t in (tools or [])}
        if "submit_report" in tool_names:
            return self._specialist(messages, tool_names)
        if "submit_consult" in tool_names:
            return self._agent(messages, tool_names)
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
            # A placeholder, not a plan: no intent is claimed (a "curative"
            # skeleton was once released for a stage IVB case), and it says
            # where the deterministic plan is.
            return self._reply(json.dumps({
                "intent": "undetermined",
                "summary": "Offline mock model: no clinical plan is drafted. "
                           "Disconnect the model to see the deterministic "
                           "rule-mode plan, or connect a real model.",
                "options": [{"name": "Mock placeholder — no regimen proposed",
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
    @staticmethod
    def _current_turn(messages: list[dict[str, Any]]) -> tuple[str, dict[str, Any], set[str]]:
        """(latest clinician message, observations by tool name, tools
        called) for the current turn of an agent loop."""
        starts = [i for i, m in enumerate(messages) if m.get("role") == "user"
                  and not str(m.get("content") or "").startswith("[系统]")]
        user = messages[starts[-1]].get("content") if starts else ""
        if isinstance(user, list):
            user = " ".join(p.get("text", "") for p in user if isinstance(p, dict))
        turn = messages[(starts[-1] + 1) if starts else 0:]
        names: dict[str, str] = {}
        seen: dict[str, Any] = {}
        called: set[str] = set()
        for message in turn:
            for call in message.get("tool_calls") or []:
                name = (call.get("function") or {}).get("name", "")
                names[call.get("id", "")] = name
                called.add(name)
            if message.get("role") == "tool":
                name = names.get(message.get("tool_call_id", ""), "")
                try:
                    seen[name] = json.loads(message.get("content") or "{}")
                except json.JSONDecodeError:
                    seen[name] = {}
        return str(user or ""), seen, called

    def _calls(self, text: str, *calls: tuple[str, dict[str, Any]]) -> LLMResponse:
        self._seq = getattr(self, "_seq", 0) + 1
        return LLMResponse(
            text=text, provider=self.name, model=self.model,
            finish_reason="tool_calls",
            tool_calls=[ToolCall(name, args, id=f"mock_{self._seq}_{i}")
                        for i, (name, args) in enumerate(calls)])

    @staticmethod
    def _english(messages: list[dict[str, Any]]) -> bool:
        """The runtime appends an "Output language" section to the system
        prompt when the clinician works in English."""
        system = next((m.get("content") or "" for m in messages
                       if m.get("role") == "system"), "")
        return "## Output language" in str(system)

    def _agent(self, messages: list[dict[str, Any]], tool_names: set[str]) -> LLMResponse:
        """Scripted lead agent. It exercises the real runtime — plan,
        parallel evidence calls, specialist delegation (when an MDT is
        asked for), memory proposals, submit → hook review → resubmit —
        while saying plainly that no clinical reasoning happened."""
        user, seen, called = self._current_turn(messages)
        # Only an explicit request convenes the MDT ("请 MDT", "/mdt"), not
        # a case that merely mentions one ("MDT判定不可切除").
        en = self._english(messages)
        mdt = "delegate" in tool_names and (
            "delegate" in user or re.search(r"请\s*(召集)?\s*(MDT|多学科)", user) is not None
            or re.search(r"\b(please\s+(convene\s+)?(an?\s+)?MDT|convene)\b", user, re.I) is not None)
        planning = "update_plan" in tool_names
        # Full autonomy offers no governed_reference: the mock then records
        # its notes and fills the decisions itself (labelled as a demo).
        autonomous = "governed_reference" not in tool_names
        source = "record_case_facts" if autonomous else "governed_reference"
        if en:
            steps = ["Understand the case and check the stage",
                     "Decide the stage, treatment intent and plan" if autonomous
                     else "Consult the governed-pipeline reference"]
            if mdt:
                steps.append("MDT: radiology and medical oncology")
            steps.append("Integrate and submit the conclusion")
        else:
            steps = ["理解病例并核对分期",
                     "判断分期、治疗意图与方案" if autonomous else "参考受治理流水线意见"]
            if mdt:
                steps.append("多学科会诊：影像科、肿瘤内科")
            steps.append("综合并提交结论")

        def plan(done: int) -> dict[str, Any]:
            return {"items": [{"content": c, "status": "completed" if i < done else
                               "in_progress" if i == done else "pending"}
                              for i, c in enumerate(steps)]}

        if planning and "update_plan" not in called:
            calls = [("update_plan", plan(0))]
            said = user.split("\n\n", 1)[0]
            marker = next((m for m in ("记住", "remember:", "Remember:", "remember")
                           if m in said), None)
            if "remember" in tool_names and marker:
                note = said.split(marker, 1)[1].strip(" ：:，,。.")[:120]
                if note:
                    calls.append(("remember", {"note": note, "scope": "preference"}))
            return self._calls("Planning the consult first." if en else "先制定会诊计划。",
                               *calls)
        if source not in seen:
            if autonomous:
                from ..conversation import extract_facts_deterministic

                calls = [("record_case_facts", {
                    "facts": extract_facts_deterministic(user),
                    "note": "offline mock: notes read from the message"})]
            else:
                calls = [("governed_reference", {})]
            if mdt:
                calls += [("delegate", {"agent": "radiology", "task":
                                        "Check the staging descriptors and the imaging still "
                                        "needed" if en else "核对分期描述符与需要补做的影像检查"}),
                          ("delegate", {"agent": "medical_oncology", "task":
                                        "Assess systemic options and the impact of the driver "
                                        "status" if en else "评估全身治疗选择与驱动基因的影响"})]
            text = (("Fetching the reference opinion" + (" and consulting specialists in "
                                                          "parallel." if mdt else "."))
                    if en else "并行调取参考意见" + ("并邀请专科会诊。" if mdt else "。"))
            return self._calls(text, *calls)
        if autonomous:
            from ..agentic.toolbox import AgentToolbox

            notes = ((seen["record_case_facts"] or {}).get("data") or {}).get("case_notes") or {}
            reference = AgentToolbox(facts=notes, narrative=lambda: user) \
                .governed_reference().get("data") or {}
        else:
            reference = (seen["governed_reference"] or {}).get("data") or {}
        consult = self._mock_consult(reference, en=en, autonomous=autonomous)
        if mdt:
            consult["reply"] += ("\n\n**Specialists**: radiology and medical oncology "
                                 "sub-agents were consulted (offline mock opinions; see the "
                                 "trace)." if en else
                                 "\n\n**专科意见**：已请影像科与肿瘤内科子智能体评估（离线 Mock 意见，见会诊轨迹）。")
        review = seen.get("submit_consult") or {}
        if review.get("status") == "review":
            consult["rule_responses"] = [
                {"rule_id": f.get("rule_id", ""), "decision": "accepted",
                 "reason": "Offline mock: accepts the hooks' review as given" if en
                 else "离线 Mock：直接采纳 hooks 的复核意见"}
                for f in review.get("findings") or []]
        calls = []
        if planning and "submit_consult" not in seen:
            calls.append(("update_plan", plan(len(steps))))
        calls.append(("submit_consult", consult))
        return self._calls("", *calls)

    def _specialist(self, messages: list[dict[str, Any]], tool_names: set[str]) -> LLMResponse:
        """Scripted MDT specialist: one real tool call, then a report that
        says it is a mock."""
        _user, seen, _called = self._current_turn(messages)
        en = self._english(messages)
        system = next((m.get("content") or "" for m in messages
                       if m.get("role") == "system"), "")
        title = "专科医师"
        if "中的" in system:
            title = system.split("中的", 1)[1].split("。", 1)[0]
        if en:
            from ..i18n import translate

            title = translate(title)
        preferred = [t for t in ("cns_assessment", "assess_biomarkers", "prognosis",
                                 "guideline_search") if t in tool_names]
        if preferred and not seen:
            return self._calls(f"{title}: checking the relevant facts first." if en
                               else f"{title}：先核对相关信息。", (preferred[0], {}))
        observed = next(iter(seen.values()), {}) if seen else {}
        summary = str((observed or {}).get("summary") or "")
        if en:
            report = {"report": f"Offline mock {title} opinion: no real specialist reasoning "
                                "took place." + (f" Tool result: {summary}" if summary else ""),
                      "recommendations": ["Connect a real model for substantive specialist "
                                          "opinions"],
                      "concerns": ["Offline mock: no real clinical reasoning"]}
        else:
            report = {"report": f"离线 Mock {title}意见：没有进行真实的专科推理。"
                                + (f"工具结果：{summary}" if summary else ""),
                      "recommendations": ["接入真实模型后由专科子智能体给出实质意见"],
                      "concerns": ["离线 Mock：未做真实临床推理"]}
        return self._calls("", ("submit_report", {
            **report, "key_points": [summary] if summary else [], "confidence": "low"}))

    @staticmethod
    def _mock_consult(reference: dict[str, Any], en: bool = False,
                      autonomous: bool = False) -> dict[str, Any]:
        consult = MockLLMClient._mock_consult_text(reference, en)
        stage = consult.get("stage_group")
        if autonomous:
            banner = (("> Offline mock agent: no real model reasoning took place. To "
                       "demonstrate the interface, the stage, treatment intent and plan "
                       "below were filled in by the built-in deterministic pipeline; with a "
                       "real model connected, the model decides all of them itself.")
                      if en else
                      ("> 离线 Mock 智能体：没有进行真实的模型推理。为演示界面，以下分期、"
                       "治疗意图与方案由内置确定性流水线代填；接入真实模型后，这些都由"
                       "模型自主判断。"))
            consult["reply"] = banner + "\n" + consult["reply"].split("\n", 1)[1]
        consult["stage_rationale"] = (
            ("Offline mock: read from the AJCC/UICC 9th-edition table for the recorded TNM"
             if en else "离线 Mock：按记录的 TNM 查 AJCC/UICC 第 9 版分期表")
            if stage else ("Offline mock: the TNM on record does not determine a stage"
                           if en else "离线 Mock：现有 TNM 不足以确定分期"))
        consult["intent_rationale"] = (
            "Offline mock: follows the stage and any emergency signal; not a clinical "
            "judgement" if en else "离线 Mock：随分期与急症信号而定，不是临床判断")
        category = (reference.get("plan") or {}).get("biomarker_category") or {}
        if category.get("status"):
            consult["biomarker_category"] = list(category.get("codes") or [])
            summary = category.get("summary_en" if en else "summary_zh") or ""
            consult["biomarker_rationale"] = (
                f"Offline mock: read from the category table — {summary}" if en
                else f"离线 Mock：按分类表对照检测结果——{summary}")
            label = "**Biomarker category**: " if en else "**生物标志物分类**："
            lines = consult["reply"].split("\n")
            at = next((i for i, line in enumerate(lines)
                       if line.startswith(("**分期**", "**Stage**"))), len(lines) - 1)
            lines[at + 1:at + 1] = ["", f"{label}{summary}"]
            consult["reply"] = "\n".join(lines)
        return consult

    @staticmethod
    def _mock_consult_text(reference: dict[str, Any], en: bool = False) -> dict[str, Any]:
        if en:
            from ..i18n import localize

            return MockLLMClient._mock_consult_en(localize(reference))
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
            "intent": "emergency" if emergency else _intent(plan.get("intent")),
            "options": options,
            "workup": list(plan.get("workup_needed") or []),
            "questions": questions,
            "warnings": ["离线 Mock：未做真实临床推理"],
            "confidence": "low",
        }

    @staticmethod
    def _mock_consult_en(reference: dict[str, Any]) -> dict[str, Any]:
        """English twin of ``_mock_consult`` (deterministic strings already
        localised through the shared dictionary)."""
        plan = reference.get("plan") or {}
        staging = reference.get("staging") or {}
        stage = staging.get("stage_group")
        options = [{"name": o.get("name") or "", "rationale": o.get("rationale") or "",
                    "regimen_ids": list(o.get("regimen_ids") or []),
                    "evidence": list(plan.get("trial_refs") or []) if i == 0 else [],
                    "preferred": i == 0}
                   for i, o in enumerate(plan.get("options") or [])]
        lines = ["> Offline mock agent: no real model reasoning took place. It relays the "
                 "governed pipeline's reference opinion to demonstrate tool calls and the "
                 "hooks review. With a real model connected, the model leads the consult.",
                 "", f"**Stage**: {stage or 'not staged'}"]
        emergency = reference.get("emergency_plan")
        if emergency:
            lines += ["", "**Emergency actions**:"] + [
                f"- {a}" for a in emergency.get("immediate_actions") or []]
        if plan.get("summary"):
            lines += ["", str(plan["summary"])]
        if options:
            lines += ["", "**Reference options**:"] + [
                f"{i + 1}. {o['name']}" + (f" — {o['rationale']}" if o["rationale"] else "")
                for i, o in enumerate(options)]
        questions = list(reference.get("open_questions") or [])
        if questions:
            lines += ["", "**Still needed**:"] + [f"- {q}" for q in questions]
        return {
            "reply": "\n".join(lines),
            "assessment": str(plan.get("summary") or ""),
            "stage_group": stage, "tnm": staging.get("tnm"),
            "intent": "emergency" if emergency else _intent(plan.get("intent")),
            "options": options,
            "workup": list(plan.get("workup_needed") or []),
            "questions": questions,
            "warnings": ["Offline mock: no real clinical reasoning"],
            "confidence": "low",
        }

    def _reply(self, text: str) -> LLMResponse:
        return LLMResponse(
            text=text, provider=self.name, model=self.model,
            finish_reason="stop", prompt_tokens=0, completion_tokens=0,
        )
