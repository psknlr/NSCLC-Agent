"""AJCC/UICC 8th-edition back-mapping for trial-era compatibility.

Every landmark trial in the registry enrolled under AJCC 7/8 — before the
9th edition split N2 into N2a/N2b and M1c into M1c1/M1c2 and migrated
several T/N combinations between stage groups. Judging "is this case inside
the trial's enrolled stages?" with today's 9th-edition label is therefore
wrong in both directions: a case the trial WOULD have enrolled can look like
an extrapolation (T2bN2b: 8th-ed IIIA → 9th-ed IIIB), and vice versa.

This module answers one narrow question deterministically: given the case's
normalized 9th-edition descriptors, what stage group would the 8th edition
have assigned? The rule engine uses it to distinguish **edition migration**
(warn + note) from **biological extrapolation** (block unless declared).
It is a lookup table from Goldstraw et al., J Thorac Oncol 2016;11:39-51 —
never a second staging engine: the 9th-edition engine remains the sole
authority for the case's actual stage.
"""

from __future__ import annotations

_T_ORDER = ("T1MI", "T1A", "T1B", "T1C", "T2A", "T2B", "T3", "T4")

#: 8th-edition stage table, M0: {N: {T: group}} with N2a/b collapsed to N2.
_M0_TABLE: dict[str, dict[str, str]] = {
    "N0": {"T1MI": "IA1", "T1A": "IA1", "T1B": "IA2", "T1C": "IA3",
           "T2A": "IB", "T2B": "IIA", "T3": "IIB", "T4": "IIIA"},
    "N1": {"T1MI": "IIB", "T1A": "IIB", "T1B": "IIB", "T1C": "IIB",
           "T2A": "IIB", "T2B": "IIB", "T3": "IIIA", "T4": "IIIA"},
    "N2": {"T1MI": "IIIA", "T1A": "IIIA", "T1B": "IIIA", "T1C": "IIIA",
           "T2A": "IIIA", "T2B": "IIIA", "T3": "IIIB", "T4": "IIIB"},
    "N3": {"T1MI": "IIIB", "T1A": "IIIB", "T1B": "IIIB", "T1C": "IIIB",
           "T2A": "IIIB", "T2B": "IIIB", "T3": "IIIC", "T4": "IIIC"},
}


def eighth_edition_group(t: object, n: object, m: object) -> str | None:
    """8th-edition stage group for normalized 9th-edition descriptors.

    Returns None whenever anything is missing or unmappable — the caller
    must treat that as "cannot rule out extrapolation", never as a match.
    """
    t_key = str(t or "").upper().replace(" ", "")
    n_key = str(n or "").upper().replace(" ", "")
    m_key = str(m or "").upper().replace(" ", "")
    if not t_key or not n_key or not m_key:
        return None
    if m_key in ("M1C1", "M1C2"):
        m_key = "M1C"  # the 9th-edition split collapses back
    if m_key in ("M1A", "M1B"):
        return "IVA"
    if m_key == "M1C":
        return "IVB"
    if m_key != "M0":
        return None
    if t_key == "TIS" and n_key == "N0":
        return "0"
    if n_key in ("N2A", "N2B"):
        n_key = "N2"  # the 9th-edition split collapses back
    row = _M0_TABLE.get(n_key)
    if row is None:
        return None
    return row.get(t_key)
