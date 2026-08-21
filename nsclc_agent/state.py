"""Run state for the NSCLC harness: the single source of truth for one case run.

Fuses the two parent designs:

* From **NSCLC-Agent v0.1**: the staging/routing provenance — the computed
  stage group is carried as authoritative state, never re-derived by a model.
* From **YaoBi-Harness**: the evidence ledger with tool-declared grades, the
  claim/citation model, the hard budget, the release-status machine and
  fail-closed semantics.

Everything is serialisable so a run can be checkpointed after each node and
resumed later.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Literal

Role = Literal["patient", "oncologist", "researcher"]
RiskMode = Literal["routine", "emergency"]

#: The release-status machine. Ordered roughly by how much may be said.
ReleaseStatus = Literal[
    "emergency_action_plan",     # oncologic emergency: fixed action script, nothing else
    "needs_more_information",    # interview axes still open
    "needs_staging_workup",      # TNM descriptors unresolved; VOI plan issued
    "insufficient_evidence",     # staged but reasoning could not be evidence-bound
    "treatment_recommendation",  # stage-appropriate plan, no dose-bearing content
    "draft_for_tumor_board",     # dose-bearing plan draft awaiting MDT/physician sign-off
    "approved_by_tumor_board",   # signed per-item approval recorded
    "blocked",                   # a required axis or safety rule blocks release
    "failed_closed",
]


class EvidenceLevel(str, Enum):
    """Provenance grade of a piece of evidence.

    The grade is declared by the *tool* that produced the evidence, never by
    the agent that consumed it. ``STUB`` exists so placeholder knowledge can
    never be laundered into guideline-grade support, and ``MODEL`` exists so a
    vision read or LLM assertion can never release a claim on its own.
    """

    OBSERVED = "observed_fact"            # case facts supplied by the referrer
    PATHOLOGY = "pathology_confirmed"     # tissue/biopsy-anchored fact
    STAGING_ENGINE = "deterministic_staging"  # computed by the symbolic TNM engine
    TRIAL = "registered_trial"            # entry from the curated trial registry
    GUIDELINE = "guideline_or_label"      # licensed guideline / regulatory label
    RETRIEVAL = "live_retrieval"          # PubMed / CT.gov / openFDA lookup result
    TOOL = "tool_result"
    MODEL = "model_reasoning"             # LLM/vision output — never releasable alone
    STUB = "stub_not_for_clinical_use"
    FAILED = "failed_tool_event"


#: Grades that may never on their own justify a released clinical claim.
NON_RELEASABLE_LEVELS = {
    EvidenceLevel.STUB.value,
    EvidenceLevel.FAILED.value,
    EvidenceLevel.MODEL.value,
}


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass
class Evidence:
    evidence_id: str
    level: str
    source: str
    summary: str
    payload: dict[str, Any] = field(default_factory=dict)
    source_version: str | None = None


@dataclass
class Claim:
    """A released assertion bound to the evidence that supports it.

    ``CitationGuard`` (see :mod:`nsclc_agent.agents.critic`) rejects any
    high-stakes claim whose evidence list is empty or backed only by
    non-releasable evidence.
    """

    claim_id: str
    kind: str
    text: str
    evidence_ids: list[str] = field(default_factory=list)
    confidence: float = 0.5
    origin: str = "rule"


@dataclass
class AgentTrace:
    agent: str
    action: str
    input_summary: str = ""
    output_summary: str = ""
    evidence_ids: list[str] = field(default_factory=list)
    timestamp: str = field(default_factory=now)


@dataclass
class Task:
    task_id: str
    agent: str
    objective: str
    required_tools: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    status: str = "pending"
    origin: str = "rule"
    #: Set when a critic finding asks this task to be re-run in a later loop.
    repair_reason: str = ""


@dataclass
class Budget:
    """Hard ceilings for a single run. Nothing in this dataclass is decorative.

    Tool calls are charged only when a call actually executes, so a denied or
    policy-blocked call can never drain the budget. Every mutator is serialised
    by a lock because the MDT panel runs members concurrently and
    ``reserve_llm`` is read-check-increment — without the lock two members can
    both pass the ceiling check and both spend. The lock is deliberately not a
    dataclass field so ``asdict`` / ``Budget(**payload)`` keep working for
    checkpoints.
    """

    max_loops: int = 2
    max_tool_calls: int = 40
    max_questions: int = 10
    max_llm_calls: int = 40
    max_llm_tokens: int = 400_000
    used_tool_calls: int = 0
    used_llm_calls: int = 0
    used_llm_tokens: int = 0
    used_questions: int = 0
    loop_counts: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._lock = threading.RLock()

    @property
    def lock(self) -> "threading.RLock":
        existing = self.__dict__.get("_lock")
        if existing is None:
            existing = threading.RLock()
            self._lock = existing
        return existing

    def can_afford_tool(self) -> bool:
        with self.lock:
            return self.used_tool_calls < self.max_tool_calls

    def charge_tool(self) -> None:
        """Charge one executed tool call. Call only after the call ran."""
        with self.lock:
            self.used_tool_calls += 1

    def reserve_llm(self) -> bool:
        with self.lock:
            if (
                self.used_llm_calls >= self.max_llm_calls
                or self.used_llm_tokens >= self.max_llm_tokens
            ):
                return False
            self.used_llm_calls += 1
            return True

    def refund_llm(self) -> None:
        """Hand back a reservation that was taken but not used."""
        with self.lock:
            self.used_llm_calls = max(0, self.used_llm_calls - 1)

    def charge_llm_tokens(self, tokens: int) -> None:
        with self.lock:
            self.used_llm_tokens += max(0, int(tokens))

    def can_loop(self, node: str) -> bool:
        with self.lock:
            return self.loop_counts.get(node, 0) < self.max_loops

    def count_loop(self, node: str) -> int:
        with self.lock:
            self.loop_counts[node] = self.loop_counts.get(node, 0) + 1
            return self.loop_counts[node]

    def reserve_questions(self, n: int) -> int:
        with self.lock:
            allowed = max(0, min(n, self.max_questions - self.used_questions))
            self.used_questions += allowed
            return allowed

    def snapshot(self) -> dict[str, str]:
        with self.lock:
            return {
                "tool_calls": f"{self.used_tool_calls}/{self.max_tool_calls}",
                "llm_calls": f"{self.used_llm_calls}/{self.max_llm_calls}",
                "llm_tokens": f"{self.used_llm_tokens}/{self.max_llm_tokens}",
                "questions": f"{self.used_questions}/{self.max_questions}",
            }


@dataclass
class CaseRunState:
    """Everything one NSCLC case run knows, sees and decides."""

    complaint: str = ""
    role: Role = "oncologist"
    run_id: str = field(default_factory=lambda: f"nsclc_{uuid.uuid4().hex[:10]}")
    risk_mode: RiskMode = "routine"
    #: Structured case facts (TNM descriptors, biomarkers, ECOG, comorbidities…).
    facts: dict[str, Any] = field(default_factory=dict)
    #: Deterministic staging provenance, set only by the StagingAgent from the
    #: symbolic engine — the one field no model may ever write.
    staging: dict[str, Any] = field(default_factory=dict)
    routing: dict[str, Any] = field(default_factory=dict)
    missing_information: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    tasks: list[Task] = field(default_factory=list)
    evidence: dict[str, Evidence] = field(default_factory=dict)
    claims: list[Claim] = field(default_factory=list)
    traces: list[AgentTrace] = field(default_factory=list)
    outputs: dict[str, Any] = field(default_factory=dict)
    release_status: ReleaseStatus = "needs_more_information"
    safety_issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    budget: Budget = field(default_factory=Budget)
    planner_mode: str = "rule"
    loop_index: int = 0
    #: Radiology images attached to this run, as ``{"kind": ..., "ref": ...}``.
    #: Bytes are never stored in the state — only references — so a checkpoint
    #: on disk cannot become an image archive.
    images: list[dict[str, Any]] = field(default_factory=list)
    #: Whether the multi-speciality MDT panel may be scheduled.
    enable_panel: bool = False
    #: Whether this run may produce dose-bearing output (drafts for a tumor
    #: board). Off by default; requires role == "oncologist".
    allow_dose_planning: bool = False

    # ---------------------------------------------------------------- evidence
    def add_evidence(
        self,
        level: EvidenceLevel | str,
        source: str,
        summary: str,
        payload: dict[str, Any] | None = None,
        *,
        ok: bool = True,
        error: str | None = None,
        source_version: str | None = None,
    ) -> str:
        eid = f"E{len(self.evidence) + 1:04d}"
        data = dict(payload or {})
        data.update({"tool_ok": ok, "tool_error": error})
        if not ok:
            evidence_level = EvidenceLevel.FAILED.value
        else:
            evidence_level = str(
                level.value if isinstance(level, EvidenceLevel) else level
            )
        self.evidence[eid] = Evidence(eid, evidence_level, source, summary, data, source_version)
        return eid

    def releasable_evidence_ids(self) -> set[str]:
        return {
            eid for eid, e in self.evidence.items()
            if e.level not in NON_RELEASABLE_LEVELS
        }

    # ------------------------------------------------------------------ claims
    def add_claim(
        self,
        kind: str,
        text: str,
        evidence_ids: list[str] | None = None,
        *,
        confidence: float = 0.5,
        origin: str = "rule",
    ) -> str:
        claim_id = f"C{len(self.claims) + 1:04d}"
        self.claims.append(
            Claim(claim_id, kind, text, list(evidence_ids or []), confidence, origin)
        )
        return claim_id

    # ------------------------------------------------------------------ status
    def fail_closed(self, reason: str) -> None:
        self.release_status = "failed_closed"
        if reason not in self.safety_issues:
            self.safety_issues.append(reason)

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    def flag(self, message: str) -> None:
        if message not in self.flags:
            self.flags.append(message)

    def trace(
        self,
        agent: str,
        action: str,
        input_summary: str = "",
        output_summary: str = "",
        evidence_ids: list[str] | None = None,
    ) -> None:
        self.traces.append(
            AgentTrace(agent, action, input_summary, output_summary, evidence_ids or [])
        )

    def task_by_id(self, task_id: str) -> Task | None:
        return next((t for t in self.tasks if t.task_id == task_id), None)

    # ------------------------------------------------------------ (de)serialise
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CaseRunState":
        """Rebuild a state from :meth:`to_dict`, so runs can be resumed."""
        payload = dict(data)
        budget = Budget(**payload.pop("budget", {}) or {})
        tasks = [Task(**t) for t in payload.pop("tasks", []) or []]
        claims = [Claim(**c) for c in payload.pop("claims", []) or []]
        traces = [AgentTrace(**t) for t in payload.pop("traces", []) or []]
        evidence = {
            k: Evidence(**v) for k, v in (payload.pop("evidence", {}) or {}).items()
        }
        known = set(cls.__dataclass_fields__)
        state = cls(**{k: v for k, v in payload.items() if k in known})
        state.budget = budget
        state.tasks = tasks
        state.claims = claims
        state.traces = traces
        state.evidence = evidence
        return state
