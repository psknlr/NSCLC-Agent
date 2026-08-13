"""Deterministic staging and routing for NSCLC (AJCC/UICC 9th edition)."""

from .tnm import (
    TNM,
    StageResult,
    StagingError,
    stage,
    stage_from_strings,
    T_CATEGORIES,
    N_CATEGORIES,
    M_CATEGORIES,
)
from .router import (
    CANONICAL_STAGE_GROUPS,
    RouteResult,
    available_modules,
    expand_stage_group,
    normalize_stage_group,
    route,
    stage_groups_compatible,
)

__all__ = [
    "TNM",
    "StageResult",
    "StagingError",
    "stage",
    "stage_from_strings",
    "T_CATEGORIES",
    "N_CATEGORIES",
    "M_CATEGORIES",
    "RouteResult",
    "route",
    "available_modules",
    "normalize_stage_group",
    "expand_stage_group",
    "stage_groups_compatible",
    "CANONICAL_STAGE_GROUPS",
]
