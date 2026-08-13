"""Consultation state: what has been asked, what is known, what is left.

A :class:`ConsultSession` is the missing piece between a static ``Case`` and an
actual interview. It is a plain serialisable record — no provider, no I/O — so
a consultation can be paused, stored as JSON, resumed in another process, and
replayed in a test without a model.

The session never *decides* anything clinical. It accumulates facts, hands them
to the deterministic staging engine through :meth:`to_case`, and records the
provenance of every value: which round it arrived in, whether a regex or a
model read it out of the reply, and which question prompted it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from typing import Any, Optional

from ..case import Case
from .planner import next_questions, outstanding_gaps, staging_is_resolvable
from .slots import SLOTS_BY_KEY, STAGING_SLOTS

#: Terminal and non-terminal session states.
STATUS_GATHERING = "gathering"      # still asking
STATUS_READY = "ready"              # enough known to answer
STATUS_EXHAUSTED = "exhausted"      # out of rounds, still short
STATUS_COMPLETE = "complete"        # a recommendation has been produced

#: Slots that map onto Case fields rather than free-form extras.
_CASE_FIELDS = {
    "t_category": "t",
    "n_category": "n",
    "m_category": "m",
}


@dataclass
class ConsultTurn:
    """One exchange: what the agent asked, what came back, what it yielded."""

    round_index: int
    asked: list[dict] = field(default_factory=list)     # RankedSlot.to_dict()
    reply: Optional[str] = None
    extracted: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "round": self.round_index,
            "asked": self.asked,
            "reply": self.reply,
            "extracted": self.extracted,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ConsultTurn":
        return cls(
            round_index=int(data.get("round", 0)),
            asked=list(data.get("asked") or []),
            reply=data.get("reply"),
            extracted=dict(data.get("extracted") or {}),
            notes=list(data.get("notes") or []),
        )


@dataclass
class ConsultSession:
    """Accumulating state of one consultation."""

    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    lang: str = "zh"
    known: dict[str, Any] = field(default_factory=dict)
    turns: list[ConsultTurn] = field(default_factory=list)
    status: str = STATUS_GATHERING
    max_rounds: int = 6
    questions_per_round: int = 3
    #: free-text the consultation opened with, kept verbatim for the prompt
    presentation: str = ""
    question: str = ""
    images: list[str] = field(default_factory=list)
    case_id: Optional[str] = None
    #: where each known value came from: key -> {"round": int, "source": str}
    provenance: dict[str, dict] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    #: stage group computed at the last update, if any
    stage_group: Optional[str] = None
    #: a stage label the case arrived with (e.g. a referral letter saying
    #: "IIIB" without a TNM triple). Kept separate from ``stage_group``, which
    #: is what the engine computed — the two must not be confused, and the
    #: label is never allowed to overwrite a computed value.
    stage_group_label: Optional[str] = None

    # -- state ---------------------------------------------------------------

    @property
    def round_index(self) -> int:
        return len(self.turns)

    @property
    def rounds_remaining(self) -> int:
        return max(0, self.max_rounds - self.round_index)

    def is_finished(self) -> bool:
        return self.status in (STATUS_READY, STATUS_EXHAUSTED, STATUS_COMPLETE)

    def record(self, values: dict[str, Any], *, source: str,
               round_index: Optional[int] = None) -> dict[str, Any]:
        """Merge new facts in.

        A value from a *later* round replaces an earlier one, and the
        replacement is recorded. Refusing to overwrite makes a mis-extraction
        permanent: the clinician says "to be clear, M0", the correction is
        dropped, and the round is reported as yielding nothing. The previous
        value is kept in ``provenance`` so the correction is auditable rather
        than invisible.
        """
        accepted: dict[str, Any] = {}
        rnd = self.round_index if round_index is None else round_index
        for key, value in values.items():
            if value in (None, "", [], {}):
                continue
            previous = self.known.get(key)
            if previous not in (None, "", [], {}):
                if previous == value:
                    continue
                prior_round = (self.provenance.get(key) or {}).get("round", -1)
                if rnd <= prior_round:
                    # Same round: two readings of one reply, first wins (the
                    # deterministic pass runs first, and it is the trusted one).
                    continue
                self.notes.append(
                    f"FACT_CORRECTED[{key}]: {previous!r} → {value!r} "
                    f"(round {prior_round} → {rnd})"
                )
                self.provenance[key] = {
                    "round": rnd, "source": source,
                    "corrected_from": previous,
                    "corrected_from_round": prior_round,
                }
            else:
                self.provenance[key] = {"round": rnd, "source": source}
            self.known[key] = value
            accepted[key] = value
        return accepted

    def pending(self) -> list[str]:
        """Slot keys still worth asking about, most valuable first."""
        return [r.key for r in
                next_questions(self.known, self.stage_group,
                               limit=len(SLOTS_BY_KEY))]

    def ask(self) -> list[dict]:
        """The next batch of questions (does not advance the round counter)."""
        return [r.to_dict(self.lang) for r in
                next_questions(self.known, self.stage_group,
                               limit=self.questions_per_round)]

    def gaps(self) -> list[dict]:
        """Everything still unknown, including sub-threshold refinements."""
        return outstanding_gaps(self.known, self.stage_group, lang=self.lang)

    def staging_ready(self) -> bool:
        return staging_is_resolvable(self.known)

    def missing_staging_descriptors(self) -> list[str]:
        return [k for k in STAGING_SLOTS if not self.known.get(k)]

    # -- conversion ----------------------------------------------------------

    def to_case(self) -> Case:
        """Project the gathered facts onto a ``Case`` for the pipeline."""
        core: dict[str, Any] = {}
        extras: dict[str, Any] = {}
        for key, value in self.known.items():
            field_name = _CASE_FIELDS.get(key)
            if field_name:
                core[field_name] = value
            else:
                extras[key] = value
        case = Case(
            case_id=self.case_id or f"consult-{self.session_id}",
            stage_group=self.stage_group_label,
            presentation=self._render_presentation(),
            question=self.question or "",
            images=list(self.images),
            fields=extras,
            **core,
        )
        return case

    def can_be_answered(self) -> bool:
        """Is it worth running the pipeline on what we have?

        True for a TNM triple, a stage label, or attached films — the
        perception layer proposes descriptors the interview never got, so
        refusing before reading them would throw away the one source that
        could still stage the case. If the read comes back empty the run
        fails with STAGE_UNRESOLVED, which says so plainly.
        """
        return (self.staging_ready() or bool(self.stage_group_label)
                or bool(self.images))

    def _render_presentation(self) -> str:
        """Opening text plus a transcript of the interview, in order."""
        parts: list[str] = []
        if self.presentation.strip():
            parts.append(self.presentation.strip())
        transcript: list[str] = []
        for turn in self.turns:
            if not turn.reply:
                continue
            asked = "; ".join(a["question"] for a in turn.asked)
            if asked:
                transcript.append(f"Q{turn.round_index + 1}: {asked}")
            transcript.append(f"A{turn.round_index + 1}: {turn.reply.strip()}")
        if transcript:
            parts.append("Consultation transcript:\n" + "\n".join(transcript))
        return "\n\n".join(parts)

    def with_case(self, case: Case) -> "ConsultSession":
        """Seed a session from an existing case (facts already on file)."""
        seeded = {}
        for slot_key, field_name in _CASE_FIELDS.items():
            value = getattr(case, field_name, None)
            if value:
                seeded[slot_key] = value
        for key, value in (case.fields or {}).items():
            if key in SLOTS_BY_KEY:
                seeded[key] = value
        self.record(seeded, source="case", round_index=-1)
        if case.stage_group and not self.stage_group_label:
            self.stage_group_label = case.stage_group
        if case.presentation and not self.presentation:
            self.presentation = case.presentation
        if case.question and not self.question:
            self.question = case.question
        if case.images and not self.images:
            self.images = list(case.images)
        if case.case_id and not self.case_id:
            self.case_id = case.case_id
        return self

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "lang": self.lang,
            "status": self.status,
            "round": self.round_index,
            "max_rounds": self.max_rounds,
            "questions_per_round": self.questions_per_round,
            "case_id": self.case_id,
            "presentation": self.presentation,
            "question": self.question,
            "images": list(self.images),
            "known": dict(self.known),
            "provenance": dict(self.provenance),
            "stage_group": self.stage_group,
            "stage_group_label": self.stage_group_label,
            "turns": [t.to_dict() for t in self.turns],
            "notes": list(self.notes),
            "outstanding": self.gaps(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ConsultSession":
        session = cls(
            session_id=data.get("session_id") or uuid.uuid4().hex[:12],
            lang=data.get("lang", "zh"),
            known=dict(data.get("known") or {}),
            status=data.get("status", STATUS_GATHERING),
            max_rounds=int(data.get("max_rounds", 6)),
            questions_per_round=int(data.get("questions_per_round", 3)),
            presentation=data.get("presentation", "") or "",
            question=data.get("question", "") or "",
            images=list(data.get("images") or []),
            case_id=data.get("case_id"),
            provenance=dict(data.get("provenance") or {}),
            notes=list(data.get("notes") or []),
            stage_group=data.get("stage_group"),
            stage_group_label=data.get("stage_group_label"),
        )
        session.turns = [ConsultTurn.from_dict(t)
                         for t in (data.get("turns") or [])]
        return session
