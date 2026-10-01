"""Model-led consultation: the model reasons, calls tools and decides.

``AgentSession`` is the agent-mode counterpart of
:class:`~nsclc_agent.conversation.ConsultationSession`. Where the governed
pipeline lets a model *propose* inside a control plane that decides, here
the model runs the consult — a ReAct loop over :class:`AgentToolbox` with
the conversation as memory — and the deterministic kernel serves it:

* tools are instruments (staging engine, registries, gates, guideline KG…);
* the rule engine and the governed pipeline are second opinions;
* when the model submits, the rule engine reviews the plan ONCE: findings
  go back to the model, which either revises or answers each with a
  clinical reason. Nothing is blocked, rewritten or withheld — findings
  and the model's answers are shown to the clinician side by side;
* an emergency screen runs on every message and is put in front of the
  model (and the clinician), not in place of it.

Everything is observable: thinking (when the provider exposes it), every
tool call and its result, the review round. ``on_event`` streams those
live (the web app renders them as a timeline).
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ..llm.base import LLMError
from .toolbox import SUBMIT_TOOL, AgentToolbox, compact

SESSION_FORMAT = "nsclc-agent-session/1"

_ROLE_TEXT = {
    "oncologist": "肿瘤科医师。用专业语言；可以讨论具体方案、证据强度与方案库的参考剂量（注明须核对说明书与本院方案）。",
    "researcher": "研究者。可以深入讨论证据、试验设计、人群外推与不确定性。",
    "patient": "患者本人或家属。通俗、清晰、有同理心；解释为什么；不要给出可自行执行的剂量，治疗决定请与主治团队讨论。",
}

SYSTEM_PROMPT = """你是 NSCLC-Agent（IMPF-AI 研发），非小细胞肺癌多学科会诊智能体。由你主导这次会诊：自主思考、规划、调用工具、权衡证据，并给出你的临床判断。

工作方式
1. 理解病例与临床问题。用 record_case_facts 维护结构化病例笔记——它就是医生看到的病例档案，也是其他工具的默认输入；新信息出现就更新它。
2. 按需调用工具：分期引擎、急症筛查、驱动基因解析、试验注册表、方案库与参考剂量、适应证与器官功能核对、脑转移分层、后线序贯、临床路径章节、指南知识库、预后、药物相互作用、读片/读报告……工具结果是证据和参考：可以采纳，也可以基于临床理由不同意，但要说明理由。
3. 规则引擎（rule_review）和受治理流水线（governed_reference）是第二意见，用来查漏补缺，不是硬约束。
4. 信息不足时，基于已有信息给出初步判断，同时明确列出还需要的检查和问题；不要编造检查结果。
5. 只把工具返回过的试验/指南条目当作引用；生存率、HR、剂量等数字要来自工具结果，或明确标注为一般医学知识。
6. 出现急症信号时，先处理急症。
7. 完成后调用一次 submit_consult。reply 是医生读到的完整回答：中文、Markdown、结构清晰（结论 → 依据 → 方案 → 待补充），简洁而专业。提交后若收到规则引擎的复核意见，逐条判断：采纳就修改方案，不采纳就在 rule_responses 写明临床理由，然后再次提交。

当前对话视角：{role_text}"""

_THINK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL | re.IGNORECASE)


@dataclass
class AgentTurn:
    """One turn of a model-led consult."""

    reply: str
    consult: dict[str, Any]
    steps: list[dict[str, Any]]
    review: dict[str, Any]
    emergency: dict[str, Any] | None
    facts: dict[str, Any]
    engine_stage: dict[str, Any] | None
    llm_calls: int
    tokens: dict[str, int]
    model: str
    duration_s: float
    error: str | None = None
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": "agent", "reply": self.reply, "consult": self.consult,
            "steps": self.steps, "review": self.review,
            "emergency": self.emergency, "facts": self.facts,
            "engine_stage": self.engine_stage, "llm_calls": self.llm_calls,
            "tokens": self.tokens, "model": self.model,
            "duration_s": round(self.duration_s, 3), "error": self.error,
            "message": self.message,
        }


@dataclass
class _TurnState:
    steps: list[dict[str, Any]] = field(default_factory=list)
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str = ""
    review_rounds: int = 0
    findings: list[dict[str, Any]] = field(default_factory=list)
    submission: dict[str, Any] | None = None


class AgentSession:
    """A multi-turn, model-led consultation."""

    def __init__(self, llm: Any, *, role: str = "oncologist",
                 vision_llm: Any | None = None, max_steps: int = 16,
                 max_review_rounds: int = 1, temperature: float = 0.2,
                 max_tokens: int = 6000,
                 on_event: Callable[[dict[str, Any]], None] | None = None) -> None:
        if llm is None or not getattr(llm, "available", False):
            raise LLMError("agent mode needs a configured model")
        self.llm = llm
        self.role = role if role in _ROLE_TEXT else "patient"
        self.vision_llm = vision_llm if vision_llm is not None else (
            llm if getattr(llm, "supports_vision", False) else None)
        self.max_steps = max(2, int(max_steps))
        self.max_review_rounds = max(0, int(max_review_rounds))
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.on_event = on_event
        self.narrative: list[str] = []
        self.turns: list[dict[str, Any]] = []
        self.toolbox = AgentToolbox(vision_llm=self.vision_llm,
                                    narrative=lambda: "\n".join(self.narrative))
        self.messages: list[dict[str, Any]] = []

    # ---------------------------------------------------------------- state
    @property
    def facts(self) -> dict[str, Any]:
        return self.toolbox.facts

    def _system(self) -> dict[str, Any]:
        return {"role": "system",
                "content": SYSTEM_PROMPT.format(role_text=_ROLE_TEXT[self.role])}

    def _emit(self, event: dict[str, Any]) -> None:
        if self.on_event is not None:
            try:
                self.on_event(event)
            except Exception:  # noqa: BLE001 - a UI hook never breaks a consult
                pass

    # ------------------------------------------------------------------ turn
    def turn(self, message: str, *, facts: dict[str, Any] | None = None,
             images: list[str] | None = None,
             reports: list[str] | None = None) -> AgentTurn:
        started = time.perf_counter()
        message = str(message or "").strip()
        self.narrative.append(message)
        self.toolbox.attachments = {"images": list(images or []),
                                    "reports": list(reports or [])}
        self._compact_history()
        self._emit({"type": "turn_start", "message": message})

        recorded: list[str] = []
        if facts:
            out = self.toolbox.record_case_facts(facts, note="clinician-entered")
            recorded = out["data"]["changed"] + out["data"]["notes"]
        self._seed_notes(message)

        from ..safety import emergencies

        screen = emergencies.screen("\n".join(self.narrative))
        emergency = None
        if screen.hard_hits:
            emergency = {"signals": [h["label"] for h in screen.hard_hits],
                         "pathway": emergencies.action_plan(screen)}
            self._emit({"type": "alert", "emergency": emergency})

        content = self._user_content(message, facts, recorded, emergency)
        self.messages.append({"role": "user", "content": content})
        state = _TurnState()
        error: str | None = None
        try:
            self._loop(state)
        except LLMError as exc:
            error = str(exc)
            self._emit({"type": "error", "message": error})
        finally:
            self._strip_images()

        consult = state.submission or {}
        reply = str(consult.get("reply") or "").strip()
        if not reply:
            reply = (f"本轮模型调用失败：{error}" if error else
                     "模型未提交结论（步数用尽）。请补充信息或重试。")
        responses = consult.get("rule_responses") or []
        answered = {str(r.get("rule_id")) for r in responses if isinstance(r, dict)}
        review = {
            "findings": state.findings,
            "responses": responses,
            "unanswered": [f for f in state.findings if f["rule_id"] not in answered],
            "rounds": state.review_rounds,
        }
        result = AgentTurn(
            reply=reply, consult=consult, steps=state.steps, review=review,
            emergency=emergency, facts=json.loads(json.dumps(self.facts, default=str)),
            engine_stage=self.toolbox.engine_stage(), llm_calls=state.calls,
            tokens={"prompt": state.prompt_tokens,
                    "completion": state.completion_tokens},
            model=state.model or getattr(self.llm, "model", ""),
            duration_s=time.perf_counter() - started, error=error,
            message=message)
        self.turns.append({"message": message, "reply": reply,
                           "consult": consult, "review": review,
                           "error": error})
        self._emit({"type": "turn_end", "error": error})
        return result

    # ------------------------------------------------------------------ loop
    def _loop(self, state: _TurnState) -> None:
        tools = self.toolbox.specs()
        nudged = False
        for step in range(self.max_steps):
            self._emit({"type": "llm_call", "step": step + 1})
            response = self.llm.chat(self.messages, tools=tools,
                                     temperature=self.temperature,
                                     max_tokens=self.max_tokens)
            state.calls += 1
            state.prompt_tokens += response.prompt_tokens
            state.completion_tokens += response.completion_tokens
            state.model = response.model or state.model
            thinking, visible = self._split_reasoning(response)
            if thinking:
                state.steps.append({"kind": "thinking", "text": thinking})
                self._emit({"type": "thinking", "text": thinking})
            if visible and response.tool_calls:
                state.steps.append({"kind": "message", "text": visible})
                self._emit({"type": "message", "text": visible})

            if not response.tool_calls:
                self.messages.append({"role": "assistant",
                                      "content": response.text or ""})
                if response.truncated:
                    self._continue("你的上一条输出被截断。请更简洁地继续：直接调用工具，或调用 submit_consult 提交结论。")
                    continue
                if not nudged and state.submission is None:
                    # A plain-text answer is a valid answer — but ask once
                    # for the structured submission so the dossier fills in.
                    nudged = True
                    state.submission = {"reply": visible}
                    self._continue("请调用 submit_consult 提交本轮结论（reply 可直接使用你刚才的回答）。")
                    continue
                # The latest plain answer wins — unless the model already
                # made a structured submission (then that stands).
                if visible and (state.submission is None
                                or set(state.submission) <= {"reply"}):
                    state.submission = {"reply": visible}
                return

            self.messages.append(self._assistant_message(response))
            done = False
            for call in response.tool_calls:
                call_id = call.id or f"call_{step}_{call.name}"
                if call.name == SUBMIT_TOOL:
                    observation, done = self._on_submit(call.arguments, state)
                else:
                    observation = self._run_tool(call.name, call.arguments, state)
                self.messages.append({"role": "tool", "tool_call_id": call_id,
                                      "content": observation})
            if done:
                return
        state.steps.append({"kind": "limit",
                            "text": f"reached the step budget ({self.max_steps})"})

    def _run_tool(self, name: str, arguments: dict[str, Any],
                  state: _TurnState) -> str:
        self._emit({"type": "tool_call", "name": name, "args": arguments})
        t0 = time.perf_counter()
        result = self.toolbox.execute(name, arguments)
        ms = round((time.perf_counter() - t0) * 1000)
        step = {"kind": "tool", "name": name, "args": arguments,
                "ok": result["ok"], "summary": result["summary"], "ms": ms}
        state.steps.append(step)
        self._emit({"type": "tool_result", **step})
        return compact({"ok": result["ok"], "summary": result["summary"],
                        "data": result["data"]})

    def _on_submit(self, arguments: dict[str, Any],
                   state: _TurnState) -> tuple[str, bool]:
        consult = dict(arguments) if isinstance(arguments, dict) else {}
        if not str(consult.get("reply") or "").strip():
            return compact({"status": "invalid",
                            "error": "submit_consult needs a non-empty reply"}), False
        state.submission = consult
        findings = self.toolbox.review_plan(consult)
        state.findings = findings
        answered = {str(r.get("rule_id")) for r in consult.get("rule_responses") or []
                    if isinstance(r, dict)}
        unanswered = [f for f in findings if f["rule_id"] not in answered]
        state.steps.append({"kind": "submit", "findings": len(findings),
                            "unanswered": len(unanswered)})
        self._emit({"type": "review", "findings": findings,
                    "unanswered": len(unanswered)})
        if unanswered and state.review_rounds < self.max_review_rounds:
            state.review_rounds += 1
            return compact({
                "status": "review",
                "findings": unanswered,
                "instruction": "这是规则引擎的复核意见，供你参考，不是硬约束。请逐条判断："
                               "采纳就修改方案；不采纳就在 rule_responses 写明临床理由。"
                               "然后再次调用 submit_consult。",
            }), False
        return compact({"status": "accepted"}), True

    def _continue(self, text: str) -> None:
        self.messages.append({"role": "user", "content": f"[系统] {text}"})

    # --------------------------------------------------------------- helpers
    def _seed_notes(self, message: str) -> None:
        """Gap-fill the notes from unambiguous patterns in the message; the
        model sees the notes and corrects them through record_case_facts."""
        from ..conversation import (
            extract_facts_deterministic, merge_facts, sanitize_fact_payload,
        )

        try:
            extracted = extract_facts_deterministic(message)
        except Exception:  # noqa: BLE001 - seeding is best effort
            return
        cleaned, _notes = sanitize_fact_payload(extracted)
        merge_facts(self.toolbox.facts, cleaned, overwrite=False)

    def _user_content(self, message: str, facts: dict[str, Any] | None,
                      recorded: list[str],
                      emergency: dict[str, Any] | None) -> Any:
        parts = [message or "（无文字，仅附件/结构化事实）"]
        if facts:
            parts.append("【医生录入的结构化事实（已写入病例笔记）】\n"
                         + json.dumps(facts, ensure_ascii=False))
            notes = [n for n in recorded if isinstance(n, str) and ":" in n]
            if notes:
                parts.append("【录入校验提示】" + "；".join(notes))
        if emergency:
            parts.append("【系统急症筛查·供你判断】命中："
                         + "、".join(emergency["signals"])
                         + "。标准处置路径：" + json.dumps(
                             emergency["pathway"].get("immediate_actions"),
                             ensure_ascii=False))
        attachments = self.toolbox.attachments
        n_att = len(attachments["images"]) + len(attachments["reports"])
        if n_att:
            parts.append(f"【本轮附件】{len(attachments['images'])} 张影像、"
                         f"{len(attachments['reports'])} 份报告图片"
                         + ("（已直接附上，也可用 read_attachment 结构化读取）"
                            if getattr(self.llm, "supports_vision", False)
                            else "（可用 read_attachment 读取）"))
        parts.append("【当前病例笔记】" + json.dumps(self.facts, ensure_ascii=False,
                                                default=str))
        text = "\n\n".join(parts)
        refs = attachments["images"] + attachments["reports"]
        if refs and getattr(self.llm, "supports_vision", False):
            from ..perception.imaging import ImagingError, load_image_refs

            try:
                urls = load_image_refs(refs)
            except ImagingError:
                urls = []
            if urls:
                return [{"type": "text", "text": text}] + [
                    {"type": "image_url", "image_url": {"url": u}} for u in urls]
        return text

    def _strip_images(self) -> None:
        """Images ride along for the turn they were sent in, then become a
        placeholder — history never re-uploads base64 slabs."""
        for message in self.messages:
            content = message.get("content")
            if isinstance(content, list):
                texts = [p.get("text", "") for p in content
                         if isinstance(p, dict) and p.get("type") == "text"]
                images = sum(1 for p in content if isinstance(p, dict)
                             and p.get("type") == "image_url")
                message["content"] = "\n".join(texts) + (
                    f"\n[{images} 张附件图片已在当轮查看]" if images else "")

    def _compact_history(self, budget_chars: int = 90_000) -> None:
        """Keep the prompt bounded: older turns collapse into a summary."""
        if not self.messages or self.messages[0].get("role") != "system":
            self.messages.insert(0, self._system())
        else:
            self.messages[0] = self._system()
        size = len(json.dumps(self.messages, ensure_ascii=False, default=str))
        if size <= budget_chars or len(self.turns) < 2:
            return
        starts = [i for i, m in enumerate(self.messages)
                  if m.get("role") == "user"
                  and not str(m.get("content") or "").startswith("[系统]")
                  and not str(m.get("content") or "").startswith("【此前会诊摘要】")]
        if len(starts) < 2:
            return
        keep_from = starts[-1]
        summary = ["【此前会诊摘要】"]
        for t in self.turns[:-1]:
            summary.append(f"医生：{t.get('message', '')[:400]}")
            summary.append(f"你的结论：{t.get('reply', '')[:800]}")
        self.messages = [self.messages[0],
                         {"role": "user", "content": "\n".join(summary)},
                         {"role": "assistant", "content": "好的，我已了解此前的会诊经过。"},
                         *self.messages[keep_from:]]

    @staticmethod
    def _split_reasoning(response: Any) -> tuple[str, str]:
        text = response.text or ""
        thinking = ""
        raw = getattr(response, "raw", None) or {}
        try:
            message = (raw.get("choices") or [{}])[0].get("message") or {}
        except (AttributeError, IndexError, TypeError):
            message = {}
        for key in ("reasoning_content", "reasoning"):
            if isinstance(message.get(key), str) and message[key].strip():
                thinking = message[key].strip()
                break
        found = _THINK_RE.findall(text)
        if found:
            thinking = "\n".join([thinking] + [f.strip() for f in found]).strip()
            text = _THINK_RE.sub("", text)
        return thinking, text.strip()

    @staticmethod
    def _assistant_message(response: Any) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": response.text or "",
            "tool_calls": [{
                "id": call.id or f"call_{i}_{call.name}",
                "type": "function",
                "function": {"name": call.name,
                             "arguments": json.dumps(call.arguments,
                                                     ensure_ascii=False)},
            } for i, call in enumerate(response.tool_calls)],
        }

    # ------------------------------------------------------- serialization
    def to_dict(self) -> dict[str, Any]:
        return {"format": SESSION_FORMAT, "mode": "agent",
                "facts": self.facts, "narrative": self.narrative,
                "turns": self.turns, "messages": self.messages[1:]}

    @classmethod
    def load(cls, data: dict[str, Any], llm: Any, *, role: str = "oncologist",
             vision_llm: Any | None = None, **kwargs: Any) -> "AgentSession":
        """Resume a session. Authority (role) comes from the CALLER, never
        from the file; the system prompt is rebuilt, never restored."""
        if not isinstance(data, dict) or data.get("format") != SESSION_FORMAT:
            raise ValueError("not an NSCLC-Agent agent session")
        session = cls(llm, role=role, vision_llm=vision_llm, **kwargs)
        from ..conversation import merge_facts, sanitize_fact_payload

        cleaned, _ = sanitize_fact_payload(dict(data.get("facts") or {}))
        merge_facts(session.toolbox.facts, cleaned, overwrite=True)
        session.narrative = [str(x) for x in data.get("narrative") or []]
        session.turns = [t for t in data.get("turns") or [] if isinstance(t, dict)]
        messages = [m for m in data.get("messages") or []
                    if isinstance(m, dict)
                    and m.get("role") in ("user", "assistant", "tool")]
        session.messages = [session._system(), *messages]
        return session
