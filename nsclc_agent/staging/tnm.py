"""Deterministic AJCC/UICC 9th-edition NSCLC staging engine (fixed core).

This module is a *pure symbolic* component: given TNM descriptors it computes
the stage group by table lookup. The language model never assigns a stage —
this engine does — which removes hallucination risk from the highest-stakes
step and makes staging fully auditable and unit-testable.

Changes over NSCLC-Agent v0.1, closing the defects found in review:

* **Classification prefix (c/p/yp) is first-class.** ADAURA/IMpower010 apply to
  pathologic stage, CheckMate 816/KEYNOTE-671 to clinical stage, post-induction
  decisions to ypTNM. A triple without its prefix cannot drive those branches,
  so the prefix is carried and defaults are explicit, never silent.
* **M must be stated.** An empty M no longer collapses to M0 — an unstated
  metastatic workup raised the single worst failure in v0.1: a patient with no
  PET-CT staged as curable IB with zero warnings. Use ``MX`` to acknowledge an
  incomplete workup; the engine then refuses a curative stage group and names
  the workup that resolves it.
* **Bare ``T1`` is refused**, matching how ``T2``/``N2``/``M1c`` were already
  refused: silently mapping T1 → T1a fabricated IA1 precision out of thin air.
* **Edition gate.** The engine implements the 9th edition only; a case labeled
  AJCC7/8 is rejected with a restaging instruction instead of being computed on
  the wrong table and stamped "9th edition".
* **Stage-label normalization** ("Stage IIIA" / "iiia" / "3A" → "IIIA") for the
  label-only path.

Reference: AJCC/UICC 9th edition (effective 1 January 2025). It retains the
8th-edition T and M1a/M1b categories but splits N2 into N2a (single mediastinal
station) / N2b (multiple stations) and M1c into M1c1 (multiple extrathoracic
metastases, one organ system) / M1c2 (multiple organ systems), which drives
real stage-group migration.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# --- Canonical descriptor vocabularies -------------------------------------

T_CATEGORIES = ("Tis", "T1a", "T1b", "T1c", "T2a", "T2b", "T3", "T4", "TX")
N_CATEGORIES = ("N0", "N1", "N2a", "N2b", "N3", "NX")
M_CATEGORIES = ("M0", "M1a", "M1b", "M1c1", "M1c2", "MX")
PREFIXES = ("c", "p", "yp", "yc", "r", "a")

SUPPORTED_EDITION = "AJCC9"
EDITION_LABEL = "AJCC/UICC 9th edition"

# Coarse T families used by the stage table.
_T_FAMILY = {
    "Tis": "Tis",
    "T1a": "T1", "T1b": "T1", "T1c": "T1",
    "T2a": "T2a", "T2b": "T2b",
    "T3": "T3", "T4": "T4",
    "TX": "TX",
}


class StagingError(ValueError):
    """Raised when TNM descriptors cannot be resolved to a stage group.

    Every message names the action that resolves the problem — the error is the
    seed of the value-of-information plan, not a dead end.
    """


@dataclass
class TNM:
    """A normalized, prefixed TNM descriptor triple."""

    t: str
    n: str
    m: str
    #: Classification prefix: c (clinical), p (pathologic), yp (post-induction
    #: pathologic), yc, r (recurrence), a (autopsy).
    prefix: str = "c"

    @classmethod
    def parse(cls, t: str, n: str, m: str, prefix: str = "c") -> "TNM":
        return cls(
            _normalize_t(t), _normalize_n(n), _normalize_m(m),
            _normalize_prefix(prefix),
        )

    def __str__(self) -> str:
        return f"{self.prefix}{self.t}{self.n}{self.m}"


@dataclass
class StageResult:
    """Result of staging, with provenance for auditing."""

    tnm: TNM
    stage_group: str
    edition: str = EDITION_LABEL
    migration_notes: list[str] = field(default_factory=list)
    descriptor_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "prefix": self.tnm.prefix,
            "t_category": self.tnm.t,
            "n_category": self.tnm.n,
            "m_category": self.tnm.m,
            "tnm": str(self.tnm),
            "stage_group": self.stage_group,
            "edition": self.edition,
            "migration_notes": list(self.migration_notes),
            "descriptor_notes": list(self.descriptor_notes),
        }


# --- Normalization ----------------------------------------------------------

def _clean(value: str) -> str:
    # Also fold full-width characters, which appear in Chinese clinical notes.
    text = "".join(str(value).split()).replace("–", "-")
    return text.translate({ord(f): ord(t) for f, t in zip(
        "ＴＮＭ０１２３４５６７８９ａｂｃｘＸ", "TNM0123456789abcxX")})


def _normalize_prefix(prefix: str) -> str:
    raw = _clean(prefix).lower()
    if not raw:
        return "c"
    if raw in PREFIXES:
        return raw
    raise StagingError(
        f"Unrecognized TNM prefix {prefix!r}: expected one of {', '.join(PREFIXES)}"
    )


def _normalize_t(t: str) -> str:
    raw = _clean(t)
    if not raw:
        raise StagingError("Empty T category")
    low = raw.lower()
    aliases = {"t1mi": "T1a", "tis": "Tis", "tx": "TX"}
    if low in aliases:
        return aliases[low]
    fix = {"T1A": "T1a", "T1B": "T1b", "T1C": "T1c", "T2A": "T2a", "T2B": "T2b",
           "T3": "T3", "T4": "T4"}
    upper = raw.upper()
    canon = raw[0].upper() + raw[1:].lower()
    if canon in T_CATEGORIES:
        return canon
    if upper in fix:
        return fix[upper]
    if upper == "T1":
        raise StagingError(
            "Ambiguous 'T1': specify T1a (≤1 cm) / T1b (>1–2 cm) / T1c (>2–3 cm)"
            " — they determine IA1 vs IA2 vs IA3; measure the lesion on thin-slice CT"
        )
    if upper == "T2":
        raise StagingError(
            "Ambiguous 'T2': specify T2a (>3–4 cm) or T2b (>4–5 cm) — "
            "they stage differently in the 9th edition"
        )
    raise StagingError(f"Unrecognized T category: {t!r}")


def _normalize_n(n: str) -> str:
    raw = _clean(n).upper()
    if not raw:
        raise StagingError(
            "Empty N category: establish nodal status (PET-CT, then EBUS-TBNA of "
            "suspicious stations where it changes intent), or pass 'NX' explicitly"
        )
    aliases = {"NX": "NX", "N0": "N0", "N1": "N1", "N3": "N3",
               "N2A": "N2a", "N2B": "N2b"}
    if raw in aliases:
        return aliases[raw]
    if raw == "N2":
        raise StagingError(
            "Ambiguous 'N2': specify N2a (single mediastinal station) or N2b "
            "(multi-station) — the 9th edition splits N2 and they stage "
            "differently (EBUS-TBNA station mapping resolves this)"
        )
    raise StagingError(f"Unrecognized N category: {n!r}")


def _normalize_m(m: str) -> str:
    raw = _clean(m).upper()
    if not raw:
        raise StagingError(
            "M category not provided: complete the metastatic workup (PET-CT + "
            "contrast brain MRI) and state M explicitly, or pass 'MX' to "
            "acknowledge an incomplete workup"
        )
    aliases = {"MX": "MX", "M0": "M0", "M1A": "M1a", "M1B": "M1b",
               "M1C1": "M1c1", "M1C2": "M1c2"}
    if raw in aliases:
        return aliases[raw]
    if raw == "M1C":
        raise StagingError(
            "Ambiguous 'M1c': specify M1c1 (multiple metastases, single organ "
            "system) or M1c2 (multiple organ systems) — the 9th edition splits M1c"
        )
    if raw == "M1":
        raise StagingError("Ambiguous 'M1': specify M1a / M1b / M1c1 / M1c2")
    raise StagingError(f"Unrecognized M category: {m!r}")


_STAGE_LABEL_RE = re.compile(r"^(?:STAGE)?\s*(0|I{1,3}|IV|[1-4])\s*([ABC])?\s*([1-3])?$",
                             re.IGNORECASE)
_ROMAN = {"1": "I", "2": "II", "3": "III", "4": "IV"}


def normalize_stage_group(label: str) -> str:
    """Normalize a human-supplied stage label: 'Stage IIIA' / '3a' → 'IIIA'.

    Raises :class:`StagingError` for anything unrecognizable rather than
    letting an unroutable label fall through to a confusing module error.
    """
    raw = "".join(str(label).split()).replace("期", "")
    raw = raw.translate({ord(f): ord(t) for f, t in zip("ⅠⅡⅢⅣ", "1234")})
    if raw.lower() in ("occult", "隐匿"):
        return "Occult"
    m = _STAGE_LABEL_RE.match(raw)
    if not m:
        raise StagingError(f"Unrecognized stage group label: {label!r}")
    base, letter, sub = m.groups()
    base = base.upper()
    base = _ROMAN.get(base, base)
    if base == "0":
        return "0"
    group = base + (letter.upper() if letter else "")
    if sub:
        if group != "IA":
            raise StagingError(f"Sub-stage digit only valid for IA (got {label!r})")
        group += sub
    return group


# --- The 9th-edition stage table --------------------------------------------
#
# Keyed by (T-family, N) for M0 disease. T-family collapses T1a/b/c to "T1"
# (they share stage rows) but keeps T2a and T2b distinct (they diverge).

_M0_TABLE: dict[tuple[str, str], str] = {
    # ---- N0 ----
    ("Tis", "N0"): "0",
    ("T1", "N0"): "I",   # refined to IA1/IA2/IA3 by sub-letter below
    ("T2a", "N0"): "IB",
    ("T2b", "N0"): "IIA",
    ("T3", "N0"): "IIB",
    ("T4", "N0"): "IIIA",
    # ---- N1 ----
    ("T1", "N1"): "IIA",    # T1N1 downstaged 8th IIB -> 9th IIA
    ("T2a", "N1"): "IIB",
    ("T2b", "N1"): "IIB",
    ("T3", "N1"): "IIIA",
    ("T4", "N1"): "IIIA",
    # ---- N2a (single-station) ----
    ("T1", "N2a"): "IIB",   # T1N2a downstaged 8th IIIA -> 9th IIB
    ("T2a", "N2a"): "IIIA",
    ("T2b", "N2a"): "IIIA",
    ("T3", "N2a"): "IIIA",  # T3N2a downstaged 8th IIIB -> 9th IIIA
    ("T4", "N2a"): "IIIB",
    # ---- N2b (multi-station) ----
    ("T1", "N2b"): "IIIA",
    ("T2a", "N2b"): "IIIB",  # T2N2b upstaged 8th IIIA -> 9th IIIB
    ("T2b", "N2b"): "IIIB",
    ("T3", "N2b"): "IIIB",
    ("T4", "N2b"): "IIIB",
    # ---- N3 ----
    ("T1", "N3"): "IIIB",
    ("T2a", "N3"): "IIIB",
    ("T2b", "N3"): "IIIB",
    ("T3", "N3"): "IIIC",
    ("T4", "N3"): "IIIC",
}

# 8th-edition -> 9th-edition migrations to surface for teaching/audit.
_MIGRATIONS: dict[tuple[str, str], str] = {
    ("T1", "N1"): "T1N1 downstaged from 8th-edition IIB to 9th-edition IIA.",
    ("T1", "N2a"): "T1N2a downstaged from 8th-edition IIIA to 9th-edition IIB "
                   "(still N2 mediastinal disease — manage with N2 discipline).",
    ("T3", "N2a"): "T3N2a downstaged from 8th-edition IIIB to 9th-edition IIIA.",
    ("T2a", "N2b"): "T2N2b upstaged from 8th-edition IIIA to 9th-edition IIIB.",
    ("T2b", "N2b"): "T2N2b upstaged from 8th-edition IIIA to 9th-edition IIIB.",
}

_IA_SUBSTAGE = {"T1a": "IA1", "T1b": "IA2", "T1c": "IA3"}


def stage(tnm: TNM, *, edition: str = SUPPORTED_EDITION) -> StageResult:
    """Compute the 9th-edition stage group for a normalized TNM triple.

    ``edition`` is the edition the *input descriptors* were assigned under.
    Anything other than AJCC9 is refused: computing 8th-edition descriptors on
    the 9th-edition table and stamping the result "9th edition" is precisely
    the staging-edition trap the protocol modules warn about.
    """
    if str(edition).upper().replace("-", "").replace("_", "") not in ("AJCC9", "AJCC/UICC9", "TNM9", "9"):
        raise StagingError(
            f"This engine implements the AJCC/UICC 9th edition only; the case is "
            f"labeled {edition!r}. Restage the descriptors under the 9th edition "
            f"(N2 must be split into N2a/N2b, M1c into M1c1/M1c2) before staging."
        )

    t, n, m = tnm.t, tnm.n, tnm.m
    notes: list[str] = []
    migrations: list[str] = []

    # Metastatic disease dominates the stage group regardless of T/N.
    if m in ("M1a", "M1b"):
        notes.append(
            "M1a (intrathoracic) / M1b (single extrathoracic metastasis) → Stage IVA."
        )
        return StageResult(tnm, "IVA", descriptor_notes=notes)
    if m in ("M1c1", "M1c2"):
        detail = (
            "M1c1 = multiple extrathoracic metastases, single organ system"
            if m == "M1c1"
            else "M1c2 = multiple extrathoracic metastases, multiple organ "
                 "systems (independently poorer prognosis)"
        )
        notes.append(f"{detail} → Stage IVB.")
        return StageResult(tnm, "IVB", descriptor_notes=notes)
    if m == "MX":
        raise StagingError(
            "M category is MX (indeterminate): complete the metastatic workup "
            "(PET-CT + contrast brain MRI) before assigning a curative stage group"
        )

    # M0 disease.
    if n == "NX":
        raise StagingError(
            "N category is NX (indeterminate): establish nodal status "
            "(invasive mediastinal staging where it changes treatment intent)"
        )
    if t == "TX":
        if n == "N0":
            return StageResult(
                tnm, "Occult",
                descriptor_notes=["TX N0 M0 → occult carcinoma; localize the "
                                  "primary (bronchoscopy, thin-slice CT) before "
                                  "selecting a treatment pathway."])
        raise StagingError(
            "TX with node-positive disease cannot be staged: localize and "
            "characterize the primary first"
        )

    family = _T_FAMILY[t]
    key = (family, n)
    if key not in _M0_TABLE:
        raise StagingError(f"No 9th-edition stage row for {t} {n} {m}")

    group = _M0_TABLE[key]

    # Refine Stage I into IA1/IA2/IA3/IB by T sub-letter.
    if group == "I":
        group = _IA_SUBSTAGE.get(t, "IB")

    if key in _MIGRATIONS:
        migrations.append(_MIGRATIONS[key])

    if n in ("N2a", "N2b"):
        notes.append(
            "N2 subcategory is decision-relevant in the 9th edition "
            "(N2a single-station vs N2b multi-station)."
        )
    if tnm.prefix == "yp":
        notes.append(
            "ypTNM (post-induction pathologic stage): interpret against the "
            "pre-treatment clinical stage; pathologic response (pCR/MPR) is the "
            "stronger prognostic signal after neoadjuvant therapy."
        )

    return StageResult(tnm, group, migration_notes=migrations,
                       descriptor_notes=notes)


def stage_from_strings(
    t: str, n: str, m: str, *, prefix: str = "c", edition: str = SUPPORTED_EDITION
) -> StageResult:
    """Convenience: normalize raw descriptor strings and stage them.

    Unlike v0.1, ``m`` has no default — the caller must state the metastatic
    status (or pass ``"MX"`` and accept that no curative stage group results).
    """
    return stage(TNM.parse(t, n, m, prefix=prefix), edition=edition)
