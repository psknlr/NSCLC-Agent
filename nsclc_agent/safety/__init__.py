"""Deterministic safety layer: emergency screen + treatment-plan rule engine."""

from . import emergencies, rules
from .rules import DOSE_RE, Violation, check_plan

__all__ = ["emergencies", "rules", "check_plan", "Violation", "DOSE_RE"]
