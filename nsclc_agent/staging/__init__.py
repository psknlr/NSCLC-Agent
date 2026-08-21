"""Deterministic staging and routing for NSCLC (AJCC/UICC 9th edition)."""

from .router import RouteResult, available_modules, route, stages_for_module
from .tnm import (
    M_CATEGORIES,
    N_CATEGORIES,
    T_CATEGORIES,
    StageResult,
    StagingError,
    TNM,
    normalize_stage_group,
    stage,
    stage_from_strings,
)

__all__ = [
    "TNM",
    "StageResult",
    "StagingError",
    "stage",
    "stage_from_strings",
    "normalize_stage_group",
    "T_CATEGORIES",
    "N_CATEGORIES",
    "M_CATEGORIES",
    "RouteResult",
    "route",
    "available_modules",
    "stages_for_module",
]
