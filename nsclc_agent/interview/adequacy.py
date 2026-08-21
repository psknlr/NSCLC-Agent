"""When may the enquiry stop? An independent judge decides, not the asker.

A model that decides for itself when it has asked enough will stop early —
the single most predictable failure of autonomous history-taking. The model's
"病史已充分" is therefore a *proposal* judged by an independent verifier, with
a non-progress detector so the loop cannot spin forever either.

Verdicts:

``ACHIEVED``      the verifier agrees. Proceed.
``NOT_ACHIEVED``  named gaps remain. Ask again with those gaps fed back.
``STALLED``       consecutive rounds surfaced the *same* gaps — more asking is
                  not producing answers. Proceed with the deficit recorded.
``CAP_REACHED``   the round cap fired. Same handling as STALLED.
``BLOCKED``       a required RED_FLAG axis is still open. Never waivable — not
                  by the model, not by the cap, not by a verifier outage.

The asymmetry is deliberate: everything else fails open (a patient trapped in
an endless questionnaire is also a harm), but an unanswered emergency screen
can never release a treatment-bearing result.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ..llm.base import LLMError
from .axes import AXES_BY_ID, coverage, required_open_axes

MAX_ROUNDS = 6

#: Consecutive rounds with an identical open-gap set before ``STALLED``.
#: Three, not two: answering one thing at a time is normal cooperation.
STALL_THRESHOLD = 3

ACHIEVED = "achieved"
NOT_ACHIEVED = "not_achieved"
STALLED = "stalled"
CAP_REACHED = "cap_reached"
BLOCKED = "blocked"

VERIFIER_SYSTEM_PROMPT = """你是胸部肿瘤 MDT 的**病史充分性审核者**，不是问诊者。

你唯一的任务：判断目前采集到的病史与检查信息，是否足以支撑下一步临床决策。
请以一位**苛刻的胸外科/肿瘤内科主任**的标准审核——默认立场是"还不够"。

审核要点：
1. 肿瘤急症筛查是否逐条问过并得到明确回答（"没提到"不等于阴性）。
2. TNM 三个描述符是否各自有影像/病理依据（PET-CT、脑MRI、EBUS 站点图）。
3. Tier-A 生物标志物（EGFR/ALK/PD-L1）是否足以支撑将要做的全身治疗决策。
4. 体能状态与器官功能是否足以判断拟议治疗强度的可行性。
5. 病史中是否存在互相矛盾之处需要澄清。

只输出 JSON：
{{"adequate": true/false,
  "missing_axes": ["axis_id", ...],
  "reason": "一句话说明依据",
  "contradictions": ["如有矛盾，逐条列出"]}}

`missing_axes` 只能取自这些 axis_id：{axis_ids}
不要输出诊断、治疗建议或任何剂量。"""


@dataclass
class AdequacyVerdict:
    verdict: str
    reason: str = ""
    missing_axes: list[str] = field(default_factory=list)
    #: Required axes still open — the unwaivable set.
    blocking_axes: list[str] = field(default_factory=list)
    contradictions: list[str] = field(default_factory=list)
    rounds_used: int = 0
    #: ``rule`` when no model ran, ``llm`` when the verifier answered,
    judged_by: str = "rule"

    @property
    def may_proceed(self) -> bool:
        return self.verdict in (ACHIEVED, STALLED, CAP_REACHED)

    @property
    def deficit(self) -> bool:
        return self.verdict in (STALLED, CAP_REACHED)

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict, "reason": self.reason,
            "missing_axes": self.missing_axes,
            "blocking_axes": self.blocking_axes,
            "contradictions": self.contradictions,
            "rounds_used": self.rounds_used, "judged_by": self.judged_by,
            "missing_labels": [
                AXES_BY_ID[a].label for a in self.missing_axes if a in AXES_BY_ID
            ],
        }


class AdequacyJudge:
    """Judges whether enquiry may stop; remembers gap sets for stall detection."""

    def __init__(
        self,
        llm: Any | None = None,
        *,
        max_rounds: int = MAX_ROUNDS,
        stall_threshold: int = STALL_THRESHOLD,
    ) -> None:
        self.llm = llm
        self.max_rounds = max_rounds
        self.stall_threshold = stall_threshold
        self.history: list[frozenset[str]] = []

    def judge(
        self,
        facts: dict[str, Any],
        complaint: str,
        *,
        prescriptive: bool = False,
        rounds_used: int = 0,
        budget: Any | None = None,
    ) -> AdequacyVerdict:
        required_open = required_open_axes(facts, complaint, prescriptive=prescriptive)
        blocking = [a.axis_id for a in required_open if a.tier == "RED_FLAG"]
        rule_missing = [a.axis_id for a in required_open]

        model_view = self._ask_model(facts, complaint, budget=budget)
        if model_view is None:
            missing, reason, contradictions, judged_by = (
                rule_missing, "规则审核：按必答轴判定", [], "rule")
        else:
            model_missing, reason, contradictions = model_view
            # The model may *add* gaps, never remove a rule-required one.
            missing = sorted(set(rule_missing) | set(model_missing))
            judged_by = "llm"

        signature = frozenset(missing)
        self.history.append(signature)

        # Blocking axes come first: nothing below can clear them.
        if blocking:
            if self._stalled(signature) or rounds_used >= self.max_rounds:
                return AdequacyVerdict(
                    BLOCKED,
                    "急症筛查轴仍未获答复——不能进入治疗建议环节",
                    missing, blocking, contradictions, rounds_used, judged_by)
            return AdequacyVerdict(
                NOT_ACHIEVED, reason, missing, blocking, contradictions,
                rounds_used, judged_by)

        if not missing and not contradictions:
            return AdequacyVerdict(ACHIEVED, reason or "病史充分", [], [], [],
                                   rounds_used, judged_by)
        if self._stalled(signature):
            return AdequacyVerdict(
                STALLED, "连续多轮追问未取得新信息，带缺口继续并记录在案",
                missing, [], contradictions, rounds_used, judged_by)
        if rounds_used >= self.max_rounds:
            return AdequacyVerdict(
                CAP_REACHED, f"已达最大追问轮次({self.max_rounds})，带缺口继续并记录在案",
                missing, [], contradictions, rounds_used, judged_by)
        return AdequacyVerdict(NOT_ACHIEVED, reason, missing, [], contradictions,
                               rounds_used, judged_by)

    # ------------------------------------------------------------- internals
    def _stalled(self, signature: frozenset[str]) -> bool:
        """True when the last N rounds had identical, non-empty gap sets."""
        if not signature or len(self.history) < self.stall_threshold:
            return False
        recent = self.history[-self.stall_threshold:]
        return all(entry == signature for entry in recent)

    def _ask_model(
        self,
        facts: dict[str, Any],
        complaint: str,
        *,
        budget: Any | None,
    ) -> tuple[list[str], str, list[str]] | None:
        """Run the adversarial verifier. ``None`` means it did not run."""
        if self.llm is None or not getattr(self.llm, "available", False):
            return None
        if budget is not None and not budget.reserve_llm():
            return None
        report = coverage(facts, complaint)
        try:
            response = self.llm.chat(
                [
                    {"role": "system", "content": VERIFIER_SYSTEM_PROMPT.format(
                        axis_ids=", ".join(sorted(AXES_BY_ID)))},
                    {"role": "user", "content": json.dumps({
                        "narrative": complaint[:4000],
                        "collected_facts": _redact(facts),
                        "axes_answered": report["answered"],
                        "axes_open": report["open"],
                    }, ensure_ascii=False)},
                ],
                temperature=0.0, max_tokens=700, response_format_json=True,
            )
            if budget is not None:
                budget.charge_llm_tokens(response.total_tokens)
        except (LLMError, Exception):  # noqa: BLE001 - fail open to the rule verdict
            if budget is not None:
                budget.refund_llm()
            return None

        payload = response.json(None)
        if not isinstance(payload, dict):
            return None
        missing = [
            a for a in (payload.get("missing_axes") or [])
            if isinstance(a, str) and a in AXES_BY_ID
        ]
        contradictions = [
            str(c) for c in (payload.get("contradictions") or []) if str(c).strip()
        ][:5]
        reason = str(payload.get("reason") or "")[:300]
        if payload.get("adequate") is False and not missing and not contradictions:
            contradictions = ["审核者认为病史不足但未指明具体缺口"]
        return missing, reason, contradictions


def _redact(facts: dict[str, Any]) -> dict[str, Any]:
    """Drop content the verifier has no business seeing (sign-offs, dose plans)."""
    return {
        k: v for k, v in facts.items()
        if k not in ("tumor_board_review", "dose_plan", "signature")
    }
