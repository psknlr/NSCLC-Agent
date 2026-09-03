"""Autonomous consultation (自主问诊): the interview loop around the pipeline.

The rest of the package answers a *complete* case. This subpackage is what
turns an incomplete one into a complete one by asking — deterministically
choosing what is still worth knowing, reading the answers, and stopping when
nothing left unasked could change the recommendation.

    slots.py     what an NSCLC work-up needs to know, and what each fact decides
    planner.py   value-of-information ordering + the stopping rule
    extract.py   free-text reply -> slot values (regex first, model second)
    session.py   accumulating, serialisable state of one consultation
"""

from .extract import extract, extract_deterministic
from .planner import (
    RankedSlot,
    is_sufficient,
    next_questions,
    outstanding_gaps,
    rank_slots,
    staging_is_resolvable,
)
from .session import (
    STATUS_COMPLETE,
    STATUS_EXHAUSTED,
    STATUS_GATHERING,
    STATUS_READY,
    ConsultSession,
    ConsultTurn,
)
from .slots import SLOTS, SLOTS_BY_KEY, SUFFICIENCY_THRESHOLD, Slot, stage_band

__all__ = [
    "ConsultSession",
    "ConsultTurn",
    "RankedSlot",
    "Slot",
    "SLOTS",
    "SLOTS_BY_KEY",
    "SUFFICIENCY_THRESHOLD",
    "STATUS_GATHERING",
    "STATUS_READY",
    "STATUS_EXHAUSTED",
    "STATUS_COMPLETE",
    "extract",
    "extract_deterministic",
    "is_sufficient",
    "next_questions",
    "outstanding_gaps",
    "rank_slots",
    "stage_band",
    "staging_is_resolvable",
]
