"""Canonical histology↔TNM↔stage concept map — the single source of truth.

Review found semantic drift: the staging engine correctly maps
``T1mi → IA1``, while three prose layers (protocol module, router docs,
planner rationale) called MIA "stage 0". Prose must never redefine disease
concepts; every layer that mentions AIS/MIA cites this map, and a test pins
each citing text against it.

Per IASLC 9th edition / WHO 5th edition:

* **AIS** (adenocarcinoma in situ) — a precursor glandular lesion — is
  ``Tis``; ``TisN0M0`` is **stage 0**.
* **MIA** (minimally invasive adenocarcinoma, ≤3 cm lepidic-predominant,
  invasion ≤5 mm) is ``T1mi``; ``T1miN0M0`` is **stage IA1** — NOT stage 0.
  It shares the near-100% DFS surgical paradigm with AIS, which is why the
  stage-0 module *discusses* it, and why the distinction must stay written.
"""

from __future__ import annotations

LESION_CONCEPTS: dict[str, dict[str, str]] = {
    "AIS": {
        "name": "adenocarcinoma in situ",
        "who": "precursor glandular lesion (WHO 5th)",
        "t_descriptor": "Tis",
        "stage_group": "0",
    },
    "MIA": {
        "name": "minimally invasive adenocarcinoma",
        "who": "≤3 cm lepidic-predominant, invasion ≤5 mm (WHO 5th)",
        "t_descriptor": "T1mi",
        "stage_group": "IA1",
    },
}


def lesion_stage(lesion: str) -> str | None:
    entry = LESION_CONCEPTS.get(str(lesion).upper())
    return entry["stage_group"] if entry else None
