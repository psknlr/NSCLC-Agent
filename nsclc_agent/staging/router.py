"""Route a computed stage group to the correct protocol module.

A dedicated protocol module ships for every treatment-bearing stage group:
Stage I (incl. Tis/AIS-MIA), II, IIIA, IIIB, IIIC, IVA and IVB. The only
intentionally unrouted state is occult carcinoma (TX N0 M0), where the primary
is not localized — the router surfaces that explicitly rather than silently
mis-routing (a category error would be a safety issue).

Stage labels supplied by a human (as opposed to computed by ``tnm.py``) are
normalized first: ``"stage iiib"``, ``"IIIB"`` and ``"3B"`` all resolve to
``IIIB``. Labels that are *ambiguous across modules* — bare ``III`` (IIIA vs
IIIB vs IIIC) and bare ``IV`` (IVA vs IVB) — are refused rather than guessed,
for the same reason the staging engine refuses bare ``N2``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# Maps a stage group -> protocol module key (the filename stem in prompts/).
_STAGE_TO_MODULE: dict[str, Optional[str]] = {
    "0": "stage1",        # Tis / AIS-MIA — handled by the Stage I module
    "Occult": None,
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

#: Canonical stage groups the engine can emit.
CANONICAL_STAGE_GROUPS: tuple[str, ...] = tuple(_STAGE_TO_MODULE)

#: Stage "families" a human label may legitimately stop at, mapped to the
#: canonical groups they cover. A family routes only when every member routes
#: to the same module — otherwise the substage is decision-relevant and the
#: label is refused.
_STAGE_FAMILIES: dict[str, tuple[str, ...]] = {
    "I": ("IA1", "IA2", "IA3", "IB"),
    "IA": ("IA1", "IA2", "IA3"),
    "II": ("IIA", "IIB"),
    "III": ("IIIA", "IIIB", "IIIC"),
    "IV": ("IVA", "IVB"),
}

# Human-readable guidance for stages without a dedicated module.
_UNROUTED_GUIDANCE: dict[str, str] = {
    "Occult": "Occult carcinoma (TX N0 M0) — the primary is not localized; "
              "complete the localization/staging workup before selecting a "
              "treatment module.",
    "III": "Stage 'III' is ambiguous: IIIA, IIIB and IIIC route to different "
           "protocol modules. Supply the substage (or the TNM triple, which "
           "the engine will stage).",
    "IV": "Stage 'IV' is ambiguous: IVA (M1a/M1b) and IVB (M1c1/M1c2) route "
          "to different protocol modules. Supply the substage (or the M "
          "category, which the engine will stage).",
}

_ARABIC_TO_ROMAN = {"0": "0", "1": "I", "2": "II", "3": "III", "4": "IV"}
_LABEL_RE = re.compile(r"^([0-4])([ABC][123]?)?$")


def normalize_stage_group(label: Optional[str]) -> Optional[str]:
    """Normalize a free-form stage label to a canonical group or family.

    Accepts ``"IIIB"``, ``"iiib"``, ``"Stage IIIB"``, ``"3B"``, ``"期IIIB"``…
    Returns the canonical key (``"IIIB"``), a family key (``"III"``), or
    ``None`` when the label cannot be understood at all.
    """
    if label is None:
        return None
    s = str(label).strip()
    if not s:
        return None
    # Strip a leading "stage"/"期"/"分期" marker in either language.
    s = re.sub(r"(?i)^\s*(stage|分期|期)\s*", "", s)
    s = re.sub(r"(?i)\s*(期|stage)\s*$", "", s)
    s = "".join(s.split()).replace("-", "").replace("_", "")
    if not s:
        return None
    up = s.upper()
    if up in ("OCCULT", "隐匿性", "隐匿"):
        return "Occult"
    if up in ("0", "IS", "TIS"):
        return "0"
    # "3B" / "4A" → "IIIB" / "IVA"
    m = _LABEL_RE.match(up)
    if m:
        up = _ARABIC_TO_ROMAN[m.group(1)] + (m.group(2) or "")
    # Canonical groups are stored with a lowercase-free spelling already.
    for canon in CANONICAL_STAGE_GROUPS:
        if up == canon.upper():
            return canon
    if up in _STAGE_FAMILIES:
        return up
    return None


def expand_stage_group(label: Optional[str]) -> tuple[str, ...]:
    """Canonical stage groups a (possibly family-level) label covers."""
    norm = normalize_stage_group(label)
    if norm is None:
        return ()
    if norm in _STAGE_FAMILIES:
        return _STAGE_FAMILIES[norm]
    return (norm,)


def stage_groups_compatible(provided: Optional[str],
                            computed: Optional[str]) -> bool:
    """Is a human-supplied stage label consistent with the computed group?

    ``"IA"`` is compatible with a computed ``"IA1"`` (the label is simply less
    specific); ``"IIIA"`` is not compatible with ``"IIIB"``. An unparseable
    label is never compatible — the caller should flag it.
    """
    if provided is None or computed is None:
        return True
    covered = expand_stage_group(provided)
    if not covered:
        return False
    return computed in covered


@dataclass
class RouteResult:
    stage_group: str
    module_key: Optional[str]
    available: bool
    note: Optional[str] = None
    fallback_module_key: Optional[str] = None
    #: the label as supplied, when normalization changed it (provenance)
    raw_stage_group: Optional[str] = None
    covered_stage_groups: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "stage_group": self.stage_group,
            "module_key": self.module_key,
            "available": self.available,
            "note": self.note,
            "fallback_module_key": self.fallback_module_key,
            "raw_stage_group": self.raw_stage_group,
            "covered_stage_groups": list(self.covered_stage_groups),
        }


def route(stage_group: str) -> RouteResult:
    """Return the protocol module for a stage group (or family label)."""
    raw = stage_group
    norm = normalize_stage_group(stage_group)
    if norm is None:
        return RouteResult(str(stage_group), None, False,
                           note=f"Unknown stage group {stage_group!r}.",
                           raw_stage_group=str(raw))
    raw_note = str(raw) if str(raw) != norm else None

    if norm in _STAGE_FAMILIES:
        members = _STAGE_FAMILIES[norm]
        modules = {_STAGE_TO_MODULE.get(m) for m in members}
        if len(modules) == 1 and None not in modules:
            # Every member routes to the same module — the substage does not
            # change the protocol, so the family label is safe to route.
            return RouteResult(norm, modules.pop(), True,
                               raw_stage_group=raw_note,
                               covered_stage_groups=members)
        return RouteResult(
            norm, None, False,
            note=_UNROUTED_GUIDANCE.get(
                norm, f"Stage {norm!r} is ambiguous across protocol modules; "
                      f"supply the substage."),
            raw_stage_group=raw_note,
            covered_stage_groups=members,
        )

    module = _STAGE_TO_MODULE[norm]
    if module is not None:
        return RouteResult(norm, module, True, raw_stage_group=raw_note,
                           covered_stage_groups=(norm,))
    return RouteResult(
        norm,
        None,
        False,
        note=_UNROUTED_GUIDANCE.get(norm, "No module available."),
        fallback_module_key=None,
        raw_stage_group=raw_note,
        covered_stage_groups=(norm,),
    )


def available_modules() -> list[str]:
    return sorted({m for m in _STAGE_TO_MODULE.values() if m})
