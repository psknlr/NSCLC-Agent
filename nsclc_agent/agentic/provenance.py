"""Provenance scans over a submitted consult (v1.4.1 red-team findings).

The stop hooks used to read only the structured fields: a trial cited in
the reply text ("Its benefit was confirmed by PHOENIX-LUNG-3"), a PMID in
the assessment, a dose in the warnings or a survival figure nobody looked
up all passed unchecked, and a dose was accepted as long as ANY regimen's
dosing had been looked up. These helpers read every free-text field a
clinician sees, and the text of every tool result, so that a reference, a
dose or a statistic can be traced to where it came from.

Pure functions; the hooks in :mod:`.hooks` decide what to report.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable

#: Free-text fields of a consult, as the clinician reads them.
TEXT_FIELDS = ("reply", "assessment", "tnm", "stage_rationale", "intent_rationale",
               "biomarker_rationale")
LIST_FIELDS = ("workup", "questions", "warnings")


def consult_texts(consult: dict[str, Any]) -> list[str]:
    """Every free-text string of a consult (options and rule responses too)."""
    out = [str(consult.get(k)) for k in TEXT_FIELDS if consult.get(k)]
    for key in LIST_FIELDS:
        items = consult.get(key)
        if isinstance(items, list):
            out += [str(x) for x in items if x]
    for option in consult.get("options") or []:
        if isinstance(option, dict):
            out += [str(option.get(k)) for k in ("name", "rationale") if option.get(k)]
    for response in consult.get("rule_responses") or []:
        if isinstance(response, dict) and response.get("reason"):
            out.append(str(response["reason"]))
    return out


def tool_text(result: Any) -> str:
    """A tool result (summary and data) as one searchable string."""
    if not isinstance(result, dict):
        return str(result or "")
    return " ".join([str(result.get("summary") or ""),
                     json.dumps(result.get("data"), ensure_ascii=False, default=str)])


def norm_ref(value: Any) -> str:
    return re.sub(r"[\s\-_‐]", "", str(value)).upper()


# ------------------------------------------------------------ references

_NCT_RE = re.compile(r"\bNCT\s*\d{8}\b", re.IGNORECASE)
_PMID_RE = re.compile(r"\bPMID\s*[:：#]?\s*(\d{5,9})\b", re.IGNORECASE)
#: A trial-shaped name: a 4+-letter stem with 2+ capitals and a number
#: ("KEYNOTE-189", "CheckMate 9LA", "IMpower010", "PHOENIX-LUNG-3").
_SHAPED_RE = re.compile(
    r"(?<![A-Za-z0-9])([A-Z][A-Za-z]{3,}(?:[-‐][A-Za-z]+)*[-‐ ]?\d{1,4}[A-Za-z]{0,3})(?![A-Za-z0-9])")
#: A bare name in brackets ("(ADAURA)") or before trial/study/研究/试验.
#: (Separators are mandatory inside the repetitions: an optional one made
#: the patterns backtrack exponentially on long capitalized runs.)
_NAME = r"[A-Z][A-Za-z0-9]{2,}(?:[-‐][A-Za-z0-9]+)*(?:\s\d{1,4}[A-Za-z]{0,3})?"
_BRACKETED_RE = re.compile(r"[（(]\s*(" + _NAME + r")\s*[)）]")
_NAMED_STUDY_RE = re.compile(
    r"(?<![A-Za-z0-9])(" + _NAME + r")\s*(?:(?:trial|study)\b|研究|试验)")
#: Capitalized clinical vocabulary that is not a trial name.
_NOT_TRIALS = frozenset("""
ECOG AJCC UICC IASLC COVID CTLA NTRK ERBB EGFR KRAS BRAF NRAS HRAS KEAP SMARCA CDKN ARID
QTCF QTC LVEF DLCO NYHA CYFRA VEGF VEGFR CTCAE RECIST NCCN ESMO ASCO CSCO NMPA SEER GOLD
CURB SOFA NEWS MASCC MMSE EORTC NSCLC SCLC SBRT SABR IMRT VMAT CCRT WBRT LDCT HRCT CTPA
VATS RATS EBUS TBNA FISH CRCL BRCA SUVMAX SUV PDL ALK ROS MET RET HER TPS CPS
""".split())
_STAGE_RE = re.compile(r"^(?:I{1,3}|IV)[ABC]?\d?$")


def _trial_like(name: str) -> bool:
    stem = re.match(r"[A-Za-z]+", name)
    stem_text = stem.group(0) if stem else ""
    if len(stem_text) < 4 or sum(ch.isupper() for ch in stem_text) < 2:
        return False
    if stem_text.upper() in _NOT_TRIALS or norm_ref(name) in _NOT_TRIALS:
        return False
    return not _STAGE_RE.match(norm_ref(name))


def references(text: str) -> list[tuple[str, str]]:
    """References written in free text: (as written, normalized key)."""
    found: dict[str, str] = {}
    for match in _NCT_RE.finditer(text):
        found.setdefault(norm_ref(match.group(0)), match.group(0))
    for match in _PMID_RE.finditer(text):
        found.setdefault("PMID" + match.group(1), match.group(0))
    for regex in (_SHAPED_RE, _BRACKETED_RE, _NAMED_STUDY_RE):
        for match in regex.finditer(text):
            name = match.group(1).strip()
            if _trial_like(name):
                found.setdefault(norm_ref(name), name)
    return [(shown, key) for key, shown in found.items()]


def reference_keys(text: str) -> set[str]:
    """Normalized keys of the references a text contains (PMIDs both bare
    and prefixed), for the evidence ledger."""
    keys = {key for _shown, key in references(text)}
    keys |= {match.group(1) for match in _PMID_RE.finditer(text)}
    return keys


def ref_seen(key: str, seen: set[str]) -> bool:
    if key in seen:
        return True
    return key.startswith("PMID") and (key[4:] in seen or f"PMID:{key[4:]}" in seen)


# ------------------------------------------------------------ statistics

_NUMBER_RE = re.compile(r"(?<![\d.])\d+(?:\.\d+)?|(?<![\d.])\.\d+")
_HR_RE = re.compile(
    r"(?:\bHR\b|hazard\s+ratio|风险比)\s*(?:of|was|is|were|=|:|：|为|是)?\s*"
    r"(?:about|approximately|around|~|≈|约)?\s*(\d*\.\d+|\d(?![\d.]))", re.IGNORECASE)
_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|％|percent\b)", re.IGNORECASE)
_MONTHS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:months?\b|mo\b|个月)", re.IGNORECASE)
#: A percentage or a duration is an outcome statistic only in an outcome
#: clause ("5-year OS about 87%"), not as a threshold or schedule
#: ("TPS ≥50%", "review in 3 months").
_OUTCOME_RE = re.compile(
    r"surviv|\bos\b|\bpfs\b|\bdfs\b|\befs\b|\borr\b|\bdcr\b|\bdor\b|response|remission|"
    r"benefit|reduc|risk|rate|median|relapse|recurrence|progression|mortality|death|"
    r"生存|缓解|获益|降低|风险|有效率|中位|复发|进展|死亡", re.IGNORECASE)
_THRESHOLD_BEFORE_RE = re.compile(r"(?:tps|pd-?l1|cps|\btc\b|\bic\b|[≥≤<>]|>=|<=)\s*$",
                                  re.IGNORECASE)
_CLAUSE_RE = re.compile(r"[。；;\n]|[.,，](?=\s|$)")


def numbers(text: str) -> set[str]:
    """Every number in a text, normalized ("0.80" → "0.8")."""
    out = set()
    for match in _NUMBER_RE.finditer(text or ""):
        try:
            out.add(f"{float(match.group(0)):g}")
        except ValueError:
            continue
    return out


def statistics(text: str) -> list[tuple[str, str]]:
    """Outcome statistics in a text: (normalized value, the clause)."""
    out: list[tuple[str, str]] = []
    for clause in _CLAUSE_RE.split(text or ""):
        for match in _HR_RE.finditer(clause):
            out.append((f"{float(match.group(1)):g}", clause.strip()))
        if not _OUTCOME_RE.search(clause):
            continue
        for regex in (_PERCENT_RE, _MONTHS_RE):
            for match in regex.finditer(clause):
                if regex is _PERCENT_RE and _THRESHOLD_BEFORE_RE.search(clause[:match.start()]):
                    continue
                out.append((f"{float(match.group(1)):g}", clause.strip()))
    return out


# ----------------------------------------------------------------- doses

#: Words in library component names that are not drugs.
_NOT_DRUGS = frozenset("""
platinum doublet platinum-doublet chemotherapy concurrent thoracic radiotherapy based
cisplatin-based platinum-based histology appropriate histology-appropriate nonsquamous
surgery residual sites weekly
""".split())
#: Generic-name endings, for drugs the library does not carry.
_DRUG_SUFFIX_RE = re.compile(
    r"\b[a-z]{3,}(?:mab|nib|platin|taxel|trexed|tecan|rasib|bine|stine|poside|rubicin)\b|"
    r"[一-鿿]{1,5}(?:替尼|单抗|铂|紫杉醇|他赛|曲塞|滨|泊苷)", re.IGNORECASE)
_RT_RE = re.compile(r"c?gy\b|戈瑞", re.IGNORECASE)


def _library():
    from ..knowledge import regimens as regimen_lib

    return regimen_lib


def _drug_words(component_drug: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z\-]{4,}", component_drug.lower())
            if w not in _NOT_DRUGS}


def drug_lexicon() -> dict[str, str]:
    """Drug word → generic name: library components and Chinese/brand aliases."""
    from ..knowledge.sequencing import AGENT_ALIASES

    lexicon = {w: w for r in _library().REGIMENS for c in r.components
               for w in _drug_words(c.drug)}
    lexicon.update(AGENT_ALIASES)
    return lexicon


def _drug_near(text: str, start: int, end: int, lexicon: dict[str, str]) -> str | None:
    """The drug a dose belongs to: the nearest drug name before it in the
    same sentence (60 characters), else right after it ("80 mg of …")."""
    lowered = text.lower()
    sentence_start = max((m.end() for m in _CLAUSE_RE.finditer(lowered, 0, start)
                          if m.group(0) not in ",，"), default=0)
    window_start = max(sentence_start, start - 60)
    before = lowered[window_start:start]
    best: tuple[int, str] | None = None
    for word, generic in lexicon.items():
        position = before.rfind(word)
        if position >= 0 and (best is None or position > best[0]):
            best = (position, generic)
    for match in _DRUG_SUFFIX_RE.finditer(before):
        if best is None or match.start() > best[0]:
            best = (match.start(), lexicon.get(match.group(0).lower(), match.group(0).lower()))
    if best:
        return best[1]
    after = lowered[end:end + 25]
    for word, generic in lexicon.items():
        if word in after:
            return generic
    match = _DRUG_SUFFIX_RE.search(after)
    return match.group(0).lower() if match else None


def dose_mentions(texts: Iterable[str]) -> list[dict[str, Any]]:
    """Dose figures in authored text, each with the drug it belongs to
    (None when no drug is named near it). Library regimen ids
    ("ccrt_60gy") are references, not doses, and are removed first."""
    from ..safety.rules import DOSE_RE, normalize_for_scan

    regimen_lib = _library()
    lexicon = drug_lexicon()
    out: list[dict[str, Any]] = []
    for raw in texts:
        text = normalize_for_scan(raw)
        for rid in sorted(regimen_lib.REGIMENS_BY_ID, key=len, reverse=True):
            text = text.replace(rid, " " * len(rid))
        for match in DOSE_RE.finditer(text):
            drug = "radiotherapy" if _RT_RE.search(match.group(0)) else \
                _drug_near(text, match.start(), match.end(), lexicon)
            out.append({"drug": drug, "text": match.group(0).strip(),
                        "numbers": numbers(re.sub(r"m[2²]", "m", match.group(0)))})
    return out


def library_numbers(regimen_ids: Iterable[str], drug: str) -> set[str] | None:
    """Numbers in the library dose strings for ``drug`` across the given
    regimens, or None when none of them contains the drug."""
    found: set[str] | None = None
    for rid in regimen_ids:
        regimen = _library().get(rid)
        for component in (regimen.components if regimen else ()):
            words = _drug_words(component.drug)
            is_rt = drug == "radiotherapy" and component.route.upper().startswith("RT")
            if is_rt or drug in words or drug in component.drug.lower():
                found = (found or set()) | numbers(
                    " ".join((component.dose, component.schedule, component.duration)))
    return found
