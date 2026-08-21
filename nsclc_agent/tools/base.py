"""Tool result model, circuit breaker and the capability broker.

Three invariants hold regardless of which agent (rule-based or LLM-driven) is
calling:

* **The broker is the only door to a tool.** Role, risk mode, skill policy,
  circuit-breaker health and budget are checked before execution, and the
  budget is charged only for calls that actually run.
* **Tools declare their own evidence grade.** A stubbed knowledge source
  returns ``STUB`` and no consumer can promote it.
* **A caller's mistake is not a tool failure.** Bad arguments and unknown tool
  names are ``recoverable``: they go back to the model with the schema, stay
  out of the ledger, and never trip the breaker.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from ..state import EvidenceLevel


@dataclass
class ToolResult:
    tool: str
    ok: bool
    summary: str
    data: dict[str, Any] = field(default_factory=dict)
    evidence_level: str = EvidenceLevel.TOOL.value
    error: str | None = None
    source_version: str | None = None
    #: True when backed by placeholder data that must never be released.
    is_stub: bool = False
    #: True for transport-style failures worth retrying.
    retryable: bool = False
    #: True when the *caller* got the call wrong (bad/missing arguments,
    #: unknown tool). The model can correct these next turn; they are not
    #: clinical events.
    recoverable: bool = False

    def resolved_level(self) -> str:
        if not self.ok:
            return EvidenceLevel.FAILED.value
        if self.is_stub:
            return EvidenceLevel.STUB.value
        return self.evidence_level

    def to_journal(self) -> dict[str, Any]:
        return {
            "tool": self.tool, "ok": self.ok, "summary": self.summary,
            "data": self.data, "evidence_level": self.evidence_level,
            "error": self.error, "source_version": self.source_version,
            "is_stub": self.is_stub, "retryable": self.retryable,
            "recoverable": self.recoverable,
        }

    @classmethod
    def from_journal(cls, name: str, payload: Any) -> "ToolResult":
        """Rebuild from the journal, defensively — a malformed record becomes a
        failed result, which the run treats as unusable evidence."""
        if not isinstance(payload, dict):
            return cls(name, False, "journal_replay_malformed",
                       error="journal entry was not an object")
        return cls(
            tool=str(payload.get("tool") or name),
            ok=bool(payload.get("ok")),
            summary=str(payload.get("summary") or ""),
            data=payload.get("data") if isinstance(payload.get("data"), dict) else {},
            evidence_level=str(payload.get("evidence_level") or EvidenceLevel.TOOL.value),
            error=payload.get("error"),
            source_version=payload.get("source_version"),
            is_stub=bool(payload.get("is_stub")),
            retryable=bool(payload.get("retryable")),
            recoverable=bool(payload.get("recoverable")),
        )


class ToolHealth:
    """Circuit breaker shared by every broker within one run.

    Lock-guarded: the MDT panel calls tools from several threads, and
    ``record_failure`` is read-modify-write — without the lock, a two-strike
    breaker is not actually one under concurrency.
    """

    def __init__(self, failure_threshold: int = 2) -> None:
        self.failure_threshold = failure_threshold
        self.consecutive_failures: dict[str, int] = {}
        self.open_circuits: set[str] = set()
        self._lock = threading.RLock()

    def is_healthy(self, tool: str) -> bool:
        with self._lock:
            return tool not in self.open_circuits

    def record_success(self, tool: str) -> None:
        with self._lock:
            self.consecutive_failures.pop(tool, None)
            self.open_circuits.discard(tool)

    def record_failure(self, tool: str) -> None:
        with self._lock:
            count = self.consecutive_failures.get(tool, 0) + 1
            self.consecutive_failures[tool] = count
            if count >= self.failure_threshold:
                self.open_circuits.add(tool)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "open_circuits": sorted(self.open_circuits),
                "consecutive_failures": dict(self.consecutive_failures),
            }


#: Tools that carry or unlock dose-bearing content. No reasoning skill, panel
#: member or patient-facing path may reach them; only the deterministic dose
#: channel does.
DOSE_CHANNEL_TOOLS = frozenset({"regimen_detail", "dose_gate_check"})

#: Tools forbidden in emergency mode — an emergency run produces the fixed
#: action plan and must not start treatment planning.
EMERGENCY_FORBIDDEN = DOSE_CHANNEL_TOOLS | {"protocol_lookup"}


class CapabilityBroker:
    """Single choke point for tool authorisation.

    Check order matters: policy first, budget last, so a denied call can never
    drain the run budget.
    """

    def __init__(
        self,
        role: str,
        risk_mode: str,
        budget: Any | None = None,
        skill_registry: Any | None = None,
        active_skill: str | None = None,
        health: ToolHealth | None = None,
        *,
        require_skill: bool = True,
        journal: Any | None = None,
    ) -> None:
        self.role = role
        self.risk_mode = risk_mode
        self.budget = budget
        self.skill_registry = skill_registry
        self.active_skill = active_skill
        self.health = health or ToolHealth()
        self.require_skill = require_skill
        #: Optional call journal — rides on the broker because the broker is
        #: already the single door every tool call passes through.
        self.journal = journal

    def allow(self, tool: str) -> tuple[bool, str]:
        """Return ``(allowed, reason)`` without consuming any budget."""
        if not self.health.is_healthy(tool):
            return False, "tool_unhealthy_circuit_open"
        if self.risk_mode == "emergency" and tool in EMERGENCY_FORBIDDEN:
            return False, "emergency_mode_forbids_treatment_planning_tools"
        if self.role == "patient" and tool in DOSE_CHANNEL_TOOLS:
            return False, "patient_role_forbids_dose_tools"
        if self.skill_registry is not None:
            # Fail closed: an agent with no declared skill has no tool rights.
            if not self.active_skill:
                if self.require_skill:
                    return False, "skill_policy_denied:no_active_skill_declared"
            else:
                ok, problems = self.skill_registry.enforce(
                    self.active_skill, self.role, [tool]
                )
                if not ok:
                    return False, "skill_policy_denied:" + ";".join(problems)
        if self.budget is not None and not self.budget.can_afford_tool():
            return False, "budget_exhausted"
        return True, "allowed"

    def charge(self) -> None:
        if self.budget is not None:
            self.budget.charge_tool()
