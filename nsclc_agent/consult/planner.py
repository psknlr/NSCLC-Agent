"""Value-of-information planning: what to ask next, and when to stop asking.

The planner is deliberately deterministic and model-free. Deciding *what is
still worth asking* is a policy question about the guideline structure, not a
generation task — keeping it in Python makes the consultation auditable (every
question carries the decision it was asked for) and reproducible across
backends, the same argument the staging engine makes for stage groups.

Ordering rule, highest first:

1. **Blocking staging descriptors.** Until T/N/M resolve, nothing downstream
   can be computed, so they outrank everything regardless of weight.
2. **Stage-conditional weight.** Once a stage band is known, each remaining
   slot is scored by what it can still change *at that stage*.
3. **Gate readiness.** Slots whose precondition is not yet met are not asked.

Stopping rule: the consultation is *sufficient* when no relevant unknown slot
scores at or above :data:`SUFFICIENCY_THRESHOLD` — i.e. nothing left unasked
can still move the recommendation. That is the honest terminating condition;
asking until every field is filled would be a form, not a consultation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from .slots import (
    SLOTS,
    STAGING_SLOTS,
    SUFFICIENCY_THRESHOLD,
    Slot,
    stage_band,
)

#: Staging descriptors are prioritised above their nominal weight while they
#: are still blocking: nothing can be staged, routed or scored without them.
_BLOCKING_BONUS = 1000


@dataclass
class RankedSlot:
    slot: Slot
    score: int
    blocking: bool

    @property
    def key(self) -> str:
        return self.slot.key

    def to_dict(self, lang: str = "zh") -> dict:
        return {
            "key": self.slot.key,
            "question": self.slot.question(lang),
            "why": self.slot.impact(lang),
            "category": self.slot.category,
            "score": self.score,
            "blocking": self.blocking,
        }


def staging_is_resolvable(known: dict[str, Any]) -> bool:
    """Can the deterministic engine actually run on what we know?

    Presence is not enough — the values must be ones the engine accepts. A
    case seeded with the ambiguous ``T2``/``N2`` that ``tnm.py`` deliberately
    refuses would otherwise look complete: the consultation would declare
    itself ready, never ask the one question that mattered, and only fail at
    the very end with a staging error.

    ``m_category`` is deliberately required: the agent will happily stage
    without it, but only by *assuming* M0, and the point of the consultation is
    to ask rather than assume.
    """
    if not all(known.get(k) for k in STAGING_SLOTS):
        return False
    return not unresolvable_staging_slots(known)


def unresolvable_staging_slots(known: dict[str, Any]) -> list[str]:
    """Staging slots holding a value the engine cannot use."""
    from ..staging.tnm import TNM, StagingError  # local: avoids a cycle

    present = {k: known.get(k) for k in STAGING_SLOTS if known.get(k)}
    if not present:
        return []
    try:
        TNM.parse(present.get("t_category") or "TX",
                  present.get("n_category") or "NX",
                  present.get("m_category") or "M0")
    except StagingError:
        # Narrow it down to the offending descriptor(s).
        bad = []
        for key, probe in (("t_category", lambda v: TNM.parse(v, "N0", "M0")),
                           ("n_category", lambda v: TNM.parse("T1a", v, "M0")),
                           ("m_category", lambda v: TNM.parse("T1a", "N0", v))):
            value = present.get(key)
            if not value:
                continue
            try:
                probe(value)
            except StagingError:
                bad.append(key)
        return bad
    return []


def rank_slots(
    known: dict[str, Any],
    stage_group: Optional[str] = None,
    *,
    slots: tuple[Slot, ...] = SLOTS,
) -> list[RankedSlot]:
    """Score every still-relevant slot, most valuable first."""
    band = stage_band(stage_group)
    # A descriptor the engine cannot use is not an answer — drop it here so the
    # planner asks again instead of treating "T2" as a settled T category.
    unusable = unresolvable_staging_slots(known)
    if unusable:
        known = {k: v for k, v in known.items() if k not in unusable}
    blocking_phase = not staging_is_resolvable(known)
    ranked: list[RankedSlot] = []
    for slot in slots:
        if not slot.is_relevant(known, band):
            continue
        score = slot.weight(band)
        blocking = blocking_phase and slot.key in STAGING_SLOTS
        if blocking:
            score += _BLOCKING_BONUS
        ranked.append(RankedSlot(slot, score, blocking))
    # Stable, deterministic order: score desc, then declaration order.
    order = {s.key: i for i, s in enumerate(slots)}
    ranked.sort(key=lambda r: (-r.score, order[r.key]))
    return ranked


def next_questions(
    known: dict[str, Any],
    stage_group: Optional[str] = None,
    *,
    limit: int = 3,
    threshold: int = SUFFICIENCY_THRESHOLD,
) -> list[RankedSlot]:
    """The next batch of questions worth a round.

    While staging is unresolved the batch is restricted to the blocking
    descriptors, so the first exchange is not diluted with biomarker questions
    that may turn out to be irrelevant once the stage is known.
    """
    ranked = [r for r in rank_slots(known, stage_group) if r.score >= threshold]
    if not ranked:
        return []
    if ranked[0].blocking:
        ranked = [r for r in ranked if r.blocking]
    return ranked[:max(1, limit)]


def is_sufficient(
    known: dict[str, Any],
    stage_group: Optional[str] = None,
    *,
    threshold: int = SUFFICIENCY_THRESHOLD,
) -> bool:
    """True when nothing still unknown can move the recommendation."""
    return not next_questions(known, stage_group, threshold=threshold)


def outstanding_gaps(
    known: dict[str, Any],
    stage_group: Optional[str] = None,
    *,
    threshold: int = SUFFICIENCY_THRESHOLD,
    lang: str = "zh",
) -> list[dict]:
    """Everything still unknown, for the record — including sub-threshold items.

    A consultation that stops is not a consultation that knows everything, and
    the residual uncertainty belongs in the output rather than being dropped.
    """
    return [
        {**r.to_dict(lang), "above_threshold": r.score >= threshold}
        for r in rank_slots(known, stage_group)
    ]
