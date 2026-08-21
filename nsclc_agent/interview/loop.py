"""The interview loop: the model asks, the judge decides when it may stop.

The model does not read out a questionnaire; it receives the open axes and
*composes* the enquiry, handing questions back through the ``ask_case_question``
tool — asking is an action the model takes, not an output the system formats.
A follow-up can therefore be conditional on the previous answer, which a static
gap table can never do.

Five gates contain it, each a real rejection path:

1. **Structure.** Questions arrive through the tool contract, each declaring
   the ``axis_id`` it closes. Unknown axis → dropped.
2. **Coverage.** Required axes are rule-derived; a skipped required axis is
   added back from the probe bank. The model chooses wording, never scope.
3. **No clinical content.** A question carrying advice, a diagnosis or a dose
   is rejected — asking is not a channel for telling.
4. **Budget.** Questions per round and rounds per interview are capped.
5. **Fallback.** No model / over budget / malformed reply → the probe bank
   verbatim. The interview degrades in wording, never in coverage.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from ..llm.base import LLMError, ToolSpec
from .adequacy import AdequacyJudge, AdequacyVerdict
from .axes import AXES_BY_ID, coverage, plan_next

MAX_QUESTIONS_PER_ROUND = 4

#: Phrasings that mean the model started advising instead of asking.
ADVICE_RE = re.compile(
    r"建议(?:你|您)?(?:服用|使用|接受|做)|应该(?:服用|接受|开始)|"
    r"诊断为|考虑为.{0,8}(?:癌|瘤|病)|推荐(?:方案|使用)|"
    r"you should (?:take|start|receive)|the diagnosis is|i recommend",
    re.IGNORECASE,
)
DOSE_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:mg/m2|mg/m²|mg/kg|mg\b|毫克|g\b|克|Gy\b)|AUC\s*\d",
    re.IGNORECASE,
)

ASK_TOOL = ToolSpec(
    "ask_case_question",
    "向病人/主管医师提出追问。每个问题必须声明它要闭合的问诊轴 axis_id。"
    "只能提问，不得在问题里给出诊断、治疗建议或任何剂量。",
    {
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "maxItems": MAX_QUESTIONS_PER_ROUND,
                "items": {
                    "type": "object",
                    "properties": {
                        "axis_id": {"type": "string", "description": "本问题要闭合的问诊轴"},
                        "question": {"type": "string", "description": "面向对方的问题"},
                        "why": {"type": "string", "description": "一句话说明为什么现在问这个（审计用）"},
                        "options": {"type": "array", "items": {"type": "string"},
                                    "description": "可选候选答案；开放性问题留空"},
                    },
                    "required": ["axis_id", "question"],
                },
            },
            "interview_complete": {
                "type": "boolean",
                "description": "认为信息已充分时置 true。该判断会被独立审核，不会直接采纳。",
            },
            "reasoning": {"type": "string", "description": "本轮为什么选这些轴（审计用）"},
        },
        "required": ["questions"],
    },
)

INTERVIEW_SYSTEM_PROMPT = """你是胸部肿瘤门诊的**问诊智能体**。你的唯一动作是"提问"，不是"作答"。

本次上下文：交付对象={role}，风险模式={risk_mode}。

## 现在要做的事
看清 `axes_open`（尚未闭合的问诊轴）与 `required_open`（必须闭合、不能跳过的轴），
然后调用 `ask_case_question` 工具提出**至多 {max_questions} 个**问题。

要求：
1. **必答轴优先。** `required_open` 里的轴必须先问；一个都不能跳。
2. **顺着上一轮答案追。** 如果对方说"PET-CT 做了"，就追"纵隔哪几站摄取增高、
   做没做 EBUS"，而不是换话题。
3. **一次只问一件事。**
4. **用对方能回答的话。** 面向患者不用"EBUS-TBNA 站点图"这类术语；面向医师可以直接用。
5. **`suggested_probes` 是素材不是台词**——结合本例改写更好，原句合适也可照用。
6. **只提问。** 问题里不得出现诊断、治疗建议、药名剂量。违反会被系统丢弃。
7. 若你认为信息确实已足够，把 `interview_complete` 置 true——但仍要给出最后值得
   确认的问题；这个判断会被独立审核者复核。"""


@dataclass
class InterviewQuestion:
    axis_id: str
    question: str
    why: str = ""
    options: list[str] = field(default_factory=list)
    origin: str = "llm"  # llm | probe_bank

    def to_dict(self) -> dict[str, Any]:
        axis = AXES_BY_ID.get(self.axis_id)
        return {
            "axis_id": self.axis_id,
            "label": axis.label if axis else self.axis_id,
            "tier": axis.tier if axis else "CONTEXT",
            "question": self.question, "why": self.why,
            "options": list(self.options), "origin": self.origin,
        }


@dataclass
class InterviewRound:
    round_index: int
    questions: list[InterviewQuestion] = field(default_factory=list)
    verdict: AdequacyVerdict | None = None
    model_claimed_complete: bool = False
    reasoning: str = ""
    rejected: list[str] = field(default_factory=list)
    composer: str = "probe_bank"

    def to_dict(self) -> dict[str, Any]:
        return {
            "round": self.round_index,
            "questions": [q.to_dict() for q in self.questions],
            "verdict": self.verdict.to_dict() if self.verdict else None,
            "model_claimed_complete": self.model_claimed_complete,
            "reasoning": self.reasoning, "rejected": self.rejected,
            "composer": self.composer,
        }


class InterviewLoop:
    """Drives one enquiry across turns, remembering what it has asked."""

    def __init__(
        self,
        llm: Any | None = None,
        *,
        judge: AdequacyJudge | None = None,
        max_questions: int = MAX_QUESTIONS_PER_ROUND,
    ) -> None:
        self.llm = llm
        self.judge = judge or AdequacyJudge(llm)
        self.max_questions = max(1, min(max_questions, MAX_QUESTIONS_PER_ROUND))
        self.rounds: list[InterviewRound] = []
        self.asked_axes: list[str] = []
        self.asked_questions: list[str] = []

    @property
    def rounds_used(self) -> int:
        return len(self.rounds)

    # -------------------------------------------------------------------- next
    def next_round(
        self,
        facts: dict[str, Any],
        complaint: str,
        *,
        role: str = "patient",
        risk_mode: str = "routine",
        prescriptive: bool = False,
        budget: Any | None = None,
    ) -> InterviewRound:
        verdict = self.judge.judge(
            facts, complaint, prescriptive=prescriptive,
            rounds_used=self.rounds_used, budget=budget,
        )
        result = InterviewRound(self.rounds_used + 1, verdict=verdict)
        if verdict.verdict in ("achieved", "stalled", "cap_reached"):
            self.rounds.append(result)
            return result

        plan = plan_next(facts, complaint, prescriptive=prescriptive,
                         limit=self.max_questions)
        # The verifier may name axes the deterministic planner did not pick.
        for axis_id in verdict.missing_axes:
            if axis_id not in plan.axis_ids and axis_id in AXES_BY_ID \
                    and len(plan.axis_ids) < self.max_questions:
                plan.axis_ids.append(axis_id)
                plan.suggested_probes.setdefault(
                    axis_id, list(AXES_BY_ID[axis_id].probes))

        composed = self._compose(plan, facts, complaint, role=role,
                                 risk_mode=risk_mode, budget=budget)
        if composed is not None:
            questions, claimed, reasoning, rejected = composed
            result.composer = "llm"
        else:
            questions, claimed, reasoning, rejected = (
                self._from_probe_bank(plan), False, "", [])
            result.composer = "probe_bank"
        if not questions:
            questions = self._from_probe_bank(plan)
            result.composer = "probe_bank"

        result.questions = questions
        result.model_claimed_complete = claimed
        result.reasoning = reasoning
        result.rejected = rejected
        self.rounds.append(result)
        for question in questions:
            if question.axis_id not in self.asked_axes:
                self.asked_axes.append(question.axis_id)
            self.asked_questions.append(question.question)
        return result

    def summary(self, facts: dict[str, Any], complaint: str,
                *, prescriptive: bool = False) -> dict[str, Any]:
        last = self.rounds[-1] if self.rounds else None
        return {
            "rounds_used": self.rounds_used,
            "coverage": coverage(facts, complaint, prescriptive=prescriptive),
            "asked_axes": list(self.asked_axes),
            "verdict": last.verdict.to_dict() if last and last.verdict else None,
            "composer": last.composer if last else "not_run",
            "rounds": [r.to_dict() for r in self.rounds],
        }

    # --------------------------------------------------------------- internals
    def _from_probe_bank(self, plan: Any) -> list[InterviewQuestion]:
        questions: list[InterviewQuestion] = []
        for axis_id in plan.axis_ids:
            axis = AXES_BY_ID.get(axis_id)
            if axis is None:
                continue
            fresh = next(
                (p for p in axis.probes if p not in self.asked_questions), None)
            questions.append(InterviewQuestion(
                axis_id, fresh or (axis.probes[0] if axis.probes else axis.label),
                why=axis.rationale, origin="probe_bank"))
            if len(questions) >= self.max_questions:
                break
        return questions

    def _compose(
        self,
        plan: Any,
        facts: dict[str, Any],
        complaint: str,
        *,
        role: str,
        risk_mode: str,
        budget: Any | None,
    ) -> tuple[list[InterviewQuestion], bool, str, list[str]] | None:
        """Ask the model to compose the round. ``None`` means it did not run."""
        if self.llm is None or not getattr(self.llm, "available", False):
            return None
        if budget is not None and not budget.reserve_llm():
            return None
        axes_open = {
            axis_id: {
                "label": AXES_BY_ID[axis_id].label,
                "tier": AXES_BY_ID[axis_id].tier,
                "why": AXES_BY_ID[axis_id].rationale,
            }
            for axis_id in plan.axis_ids if axis_id in AXES_BY_ID
        }
        payload = {
            "narrative": complaint[:4000],
            "collected_facts": {
                k: v for k, v in facts.items() if k != "tumor_board_review"
            },
            "axes_open": axes_open,
            "required_open": plan.required_open,
            "suggested_probes": plan.suggested_probes,
            "already_asked": self.asked_questions[-12:],
            "round": self.rounds_used + 1,
        }
        try:
            response = self.llm.chat(
                [
                    {"role": "system", "content": INTERVIEW_SYSTEM_PROMPT.format(
                        role=role, risk_mode=risk_mode,
                        max_questions=self.max_questions)},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                tools=[ASK_TOOL], temperature=0.3, max_tokens=1200,
            )
            if budget is not None:
                budget.charge_llm_tokens(response.total_tokens)
        except (LLMError, Exception):  # noqa: BLE001 - never break a turn
            return None

        arguments = self._ask_arguments(response)
        if arguments is None:
            return None
        return self._validate(arguments, plan)

    @staticmethod
    def _ask_arguments(response: Any) -> dict[str, Any] | None:
        """Read the ask call, tolerating a same-shaped JSON reply instead."""
        for call in getattr(response, "tool_calls", None) or []:
            if call.name == ASK_TOOL.name and isinstance(call.arguments, dict):
                return call.arguments
        payload = response.json(None) if hasattr(response, "json") else None
        return payload if isinstance(payload, dict) and "questions" in payload else None

    def _validate(
        self, arguments: dict[str, Any], plan: Any
    ) -> tuple[list[InterviewQuestion], bool, str, list[str]]:
        questions: list[InterviewQuestion] = []
        rejected: list[str] = []
        for item in arguments.get("questions") or []:
            if not isinstance(item, dict):
                rejected.append("非对象条目")
                continue
            text = str(item.get("question") or "").strip()
            axis_id = str(item.get("axis_id") or "").strip()
            if not text:
                rejected.append("空问题")
                continue
            if axis_id not in AXES_BY_ID:
                rejected.append(f"未知问诊轴 {axis_id or '(空)'}: {text[:30]}")
                continue
            if DOSE_RE.search(text):
                rejected.append(f"问题中出现剂量: {text[:30]}")
                continue
            if ADVICE_RE.search(text):
                rejected.append(f"问题中夹带诊疗建议: {text[:30]}")
                continue
            if text in self.asked_questions:
                rejected.append(f"重复提问: {text[:30]}")
                continue
            options = [str(o).strip() for o in (item.get("options") or [])
                       if str(o).strip()][:6]
            questions.append(InterviewQuestion(
                axis_id, text, why=str(item.get("why") or "")[:200],
                options=options, origin="llm"))
            if len(questions) >= self.max_questions:
                break

        # Coverage guard: a required axis the model skipped comes back from the
        # probe bank. The model chooses wording, not scope.
        covered = {q.axis_id for q in questions}
        for axis_id in plan.required_open:
            if len(questions) >= self.max_questions:
                break
            if axis_id in covered or axis_id not in AXES_BY_ID:
                continue
            axis = AXES_BY_ID[axis_id]
            fresh = next(
                (p for p in axis.probes if p not in self.asked_questions), None)
            if fresh is None:
                continue
            questions.append(InterviewQuestion(
                axis_id, fresh, why=axis.rationale, origin="probe_bank"))
            rejected.append(f"必答轴被模型遗漏，已按题库补回: {axis.label}")

        return (
            questions,
            bool(arguments.get("interview_complete")),
            str(arguments.get("reasoning") or "")[:400],
            rejected,
        )
