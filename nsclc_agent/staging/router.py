"""Route a computed stage group to its protocol module.

Every stage group the engine can produce routes somewhere — including the two
states v0.1 left dead-ended:

* Stage ``0`` (Tis/AIS-MIA) now has its own ``stage0`` module rather than being
  sent to the Stage I module whose own scope gate excludes it.
* ``Occult`` (TX N0 M0) routes to the ``workup`` module (localization protocol)
  instead of erroring out with no actionable output — the case that most needs
  a "what to check next" answer now gets one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Maps a stage group -> protocol module key (the filename stem in prompts/).
_STAGE_TO_MODULE: dict[str, str] = {
    "0": "stage0",
    "Occult": "workup",
    "IA1": "stage1",
    "IA2": "stage1",
    "IA3": "stage1",
    "IB": "stage1",
    "IIA": "stage2",
    "IIB": "stage2",
    "IIIA": "stage3a",
    "IIIB": "stage3b",
    "IIIC": "stage3c",
    "IVA": "stage4a",
    "IVB": "stage4b",
}


@dataclass
class RouteResult:
    stage_group: str
    module_key: Optional[str]
    available: bool
    note: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "stage_group": self.stage_group,
            "module_key": self.module_key,
            "available": self.available,
            "note": self.note,
        }


def route(stage_group: str) -> RouteResult:
    """Return the protocol module for a stage group."""
    if stage_group not in _STAGE_TO_MODULE:
        return RouteResult(
            stage_group, None, False,
            note=f"Unknown stage group {stage_group!r}.",
        )
    return RouteResult(stage_group, _STAGE_TO_MODULE[stage_group], True)


def available_modules() -> list[str]:
    return sorted(set(_STAGE_TO_MODULE.values()))


def stages_for_module(module_key: str) -> list[str]:
    return sorted(g for g, m in _STAGE_TO_MODULE.items() if m == module_key)
