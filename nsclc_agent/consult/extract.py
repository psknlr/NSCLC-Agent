"""Turn a free-text reply into slot values.

Two passes, in this order:

1. **Deterministic patterns** for the facts that have a canonical written form
   — TNM descriptors, ECOG, PD-L1 percentage, driver results, histology,
   resectability, age. These are regex-extracted so the consultation works
   offline, is reproducible, and does not depend on a model to read "N2b".
2. **Model-assisted extraction** for everything the patterns missed, asking
   only about the slots that are actually pending. This pass is optional: with
   no provider (or with the offline mock) the consultation still runs on pass 1.

Pass 1 always wins on conflict. A regex that matched "N2b" in the reply is more
trustworthy than a model's paraphrase of the same sentence, and the whole point
of the staging engine is not to let a model quietly restate a descriptor.
"""

from __future__ import annotations

import functools
import json
import re
from typing import Any, Optional

from ..providers.base import GenerationParams, LLMProvider, Message
from ..staging.tnm import M_CATEGORIES, N_CATEGORIES, T_CATEGORIES
from .slots import SLOTS_BY_KEY

# Marker the offline mock keys on to recognise a consultation-extraction
# request. Imported rather than restated so the two cannot drift apart — a
# desync would silently turn every model-assisted extraction into a no-op.
# (imaging.py imports IMAGING_MARKER from the same place for the same reason.)
from ..providers.mock import CONSULT_MARKER

# --------------------------------------------------------------------------- #
# Deterministic patterns                                                      #
# --------------------------------------------------------------------------- #

#: Written together ("cT2bN2bM0") the sub-letters make a plain lookbehind fail,
#: so the concatenated form is matched as a unit before the separate ones.
#: The staging prefix is captured rather than consumed so it can be checked:
#: it must be absent or lowercase (cT/pN/ypT). Consuming an UPPERCASE prefix
#: reads the "C" of "CT" as one, and "低剂量CT3年随访" becomes a T3 tumour.
_TNM_RUN_RE = re.compile(
    r"(?<![A-Za-z])([cpy]{0,2})(T(?:is|X|1mi|1[abc]|2[ab]|[34]))"
    r"\s*([cp]?)(N(?:X|0|1|2a|2b|3))"
    r"(?:\s*[cp]?(M(?:X|0|1a|1b|1c1|1c2)))?(?![a-zA-Z0-9])",
    re.IGNORECASE)
_T_RE = re.compile(
    r"(?<![A-Za-z])([cpy]{0,2})(T(?:is|X|1mi|1[abc]|2[ab]|[34]))"
    r"(?![a-zA-Z0-9])", re.IGNORECASE)
_N_RE = re.compile(
    r"(?<![A-Za-z])([cp]{0,2})(N(?:X|0|1|2a|2b|3))(?![a-zA-Z0-9])",
    re.IGNORECASE)
_M_RE = re.compile(
    r"(?<![A-Za-z])([cp]{0,2})(M(?:X|0|1a|1b|1c1|1c2))(?![a-zA-Z0-9])",
    re.IGNORECASE)


def _prefix_ok(prefix: str) -> bool:
    """A staging prefix is written lowercase (cT2b, pN0, ypT1b).

    An uppercase one is almost always the tail of another word — "CT", "PET-CT"
    — not a descriptor prefix.
    """
    return prefix == "" or prefix.islower()
#: Ambiguous forms we must *not* silently canonicalise (see staging/tnm.py).
_AMBIGUOUS_RE = re.compile(
    r"(?<![A-Za-z])[cp]?(T1|T2|N2|M1|M1c)(?![a-zA-Z0-9])")

#: "PS" is matched case-SENSITIVELY and as a whole word. Case-insensitively it
#: hides inside ordinary words — "bio(ps)y. 4.5 cm" reads as ECOG 4, i.e. a
#: bedbound patient, which is about as wrong as an extraction can be.
_ECOG_RE = re.compile(
    r"(?:(?i:ECOG)(?:[^0-9\n]{0,22}?(?i:performance\s+status|PS))?"
    r"|\bPS\b|体力状态|体能状态|一般情况|ECOG评分)"
    r"[^0-9\n]{0,8}([0-4])(?!\d)")
#: "ECOG 0-1" is a range, not a 0. Taking the first number silently records the
#: better half; the worse end is the safe reading, and it is noted.
_ECOG_RANGE_RE = re.compile(r"\s*[-–—~至到]\s*([0-4])(?!\d)")
_AGE_RE = re.compile(r"(\d{1,3})\s*(?:岁|years?\s*old|y/?o\b|yr\b)",
                     re.IGNORECASE)
_AGE_COMPACT_RE = re.compile(r"\b(\d{2,3})\s*(?:[MF]|男|女)\b")
#: The gap must be allowed to contain digits: the assay clone is usually named
#: ("PD-L1 (22C3) TPS 50%"), and forbidding digits blocked the whole match —
#: on the exact phrasing the PD-L1 question invites ("…and which assay?").
_PDL1_RE = re.compile(
    r"(?:PD-?L1|\bTPS\b|\bCPS\b)[^%\n]{0,40}?(\d{1,3})\s*%", re.IGNORECASE)
_PDL1_NEG_RE = re.compile(
    r"PD-?L1[^。;；,，]{0,20}(阴性|negative|<\s*1\s*%)", re.IGNORECASE)
#: A sentence has to be *about* nodes before bare numbers in it are read as
#: IASLC stations — otherwise "2 个淋巴结" and "7 cm" both look like stations.
_NODE_SENTENCE_RE = re.compile(
    r"[^。.;；\n]*(?:淋巴结|纵隔|站|node|nodal|station|EBUS|mediastin)"
    r"[^。.;；\n]*", re.IGNORECASE)
#: A station is unambiguous when it carries a side letter ("4R") or an explicit
#: station word ("7 组"). A bare number counts only when it is a whole list
#: element — "4R, 7 and 10L" — never when embedded like "2 个淋巴结".
_STATION_SIDED_RE = re.compile(r"(?<![\d.])(1[0-4]|[1-9])([RL])\b",
                               re.IGNORECASE)
#: English puts the word first for an identifier ("station 7") and the number
#: first for a count ("3 stations"). Matching either way round turned "EBUS
#: sampled 3 stations" into station 3 — a fabricated nodal map.
_STATION_WORDED_EN_RE = re.compile(
    r"\bstations?\s*(?:no\.?\s*)?(1[0-4]|[1-9])([RL])?\b", re.IGNORECASE)
#: Chinese writes the identifier as "7组"/"第7组"; a count carries a cue word.
_STATION_WORDED_ZH_RE = re.compile(
    r"第?\s*(1[0-4]|[1-9])([RL])?\s*(?:组|站)", re.IGNORECASE)
_ZH_COUNT_CUE_RE = re.compile(r"(?:共|计|累计|总共|个|枚|肿大|有)\s*$")
_STATION_BARE_RE = re.compile(r"^(1[0-4]|[1-9])([RL])?$", re.IGNORECASE)
_STATION_SPLIT_RE = re.compile(r"[、,，/和&—–\-:：;；]|\band\b|\+",
                               re.IGNORECASE)

#: "no distant metastasis" in the ways people actually write it. Only used when
#: no explicit M descriptor was found in the reply.
_M0_PHRASES = (
    "无远处转移", "没有远处转移", "未见远处转移", "未发现远处转移",
    "排除远处转移", "无远处播散", "远处转移阴性",
    "no distant metastas", "no evidence of distant metastas",
    "without distant metastas", "metastatic workup negative",
)
#: A whole-body staging study reported negative also establishes M0. The bar is
#: deliberately PET-CT (or PET): "brain MRI negative" alone excludes brain
#: metastases, not bone or liver, and must not be read as M0 on its own.
_M0_WORKUP_RE = re.compile(
    r"(PET[\s/-]?CT|PET)[^。.;；\n]{0,40}?"
    r"(negative|clear|unremarkable|no evidence|阴性|未见异常|未见转移|无异常)",
    re.IGNORECASE)
_M1_HINTS = (
    ("M1c2", ("多器官转移", "多个器官系统", "multiple organ systems")),
    ("M1c1", ("单器官多发转移", "multiple metastases in one organ",
              "single organ system")),
    ("M1a", ("胸膜转移", "恶性胸腔积液", "对侧肺转移", "心包转移",
             "malignant pleural effusion", "contralateral lung nodule",
             "pleural nodule", "pericardial")),
)
#: Named distant sites. Their presence means M1, but not *which* M1 — the
#: sub-category depends on lesion count and organ-system count, so the reply is
#: recorded as ambiguous rather than canonicalised (same discipline as "N2").
#: Only *named organs* count here. Generic wording like "远处转移" / "distant
#: metastasis" is deliberately excluded: it is the same phrase the M0
#: statements use ("没有远处转移"), so treating it as a site would turn every
#: negative metastatic work-up into a conflict.
_DISTANT_SITES = (
    "脑转移", "颅内转移", "骨转移", "肝转移", "肾上腺转移",
    "brain metastas", "cerebral metastas", "bone metastas",
    "liver metastas", "hepatic metastas", "adrenal metastas",
)

_POSITIVE = ("阳性", "突变", "重排", "融合", "positive", "mutant", "mutation",
             "rearrange", "fusion", "detected", "19del", "l858r", "exon20",
             "exon 20", "g12c", "v600e", "skipping")
_NEGATIVE = ("阴性", "野生", "未见", "未检出", "negative", "wild", "wt",
             "not detected", "no mutation", "absent")
_UNTESTED = ("未做", "没做", "未检测", "未查", "not tested", "not done",
             "unknown", "pending", "待回", "不详")

_HISTOLOGY = (
    ("adenosquamous", ("腺鳞", "adenosquamous")),
    ("squamous", ("鳞癌", "鳞状", "squamous")),
    ("adenocarcinoma", ("腺癌", "adenocarcinoma", "adeno")),
    ("large_cell", ("大细胞", "large cell", "large-cell")),
    ("NSCLC_NOS", ("nsclc-nos", "nsclc nos", "非小细胞肺癌未分型", "nos")),
)

#: Order matters: the "not-" rows are checked first, and every needle is
#: word-bounded (see ``_needle_re``) so "operable" cannot fire inside
#: "inoperable" — which used to invert the dominant fork of stage III.
_RESECTABILITY = (
    ("unresectable", ("不可切除", "无法切除", "不能切除", "不可手术切除",
                      "unresectable", "not resectable", "non-resectable",
                      "nonresectable", "unresected")),
    ("medically_inoperable", ("不能耐受手术", "无法耐受手术", "高危不宜手术",
                              "medically inoperable", "unfit for surgery",
                              "inoperable")),
    ("declined_surgery", ("拒绝手术", "不愿手术", "declines surgery",
                          "declined surgery", "refuses surgery",
                          "refused surgery")),
    ("resectable", ("可切除", "能切除", "可以手术", "可手术切除",
                    "resectable", "operable")),
)

_SMOKING = (
    ("current", ("目前吸烟", "仍在吸", "current smoker", "still smok")),
    ("former", ("已戒", "戒烟", "former smoker", "ex-smoker", "quit")),
    ("never", ("不吸烟", "从不吸烟", "无吸烟史", "never smoker", "non-smoker",
               "never smoked")),
)


#: Negation cues, checked immediately before a matched phrase. Without this a
#: reply that RULES OUT a finding reads as asserting it: "no malignant pleural
#: effusion" set M1a, i.e. it staged a curative patient as IVA.
_NEGATION_RE = re.compile(
    r"(?:无|未见|没有|未发现|未|否认|排除|不伴|阴性"
    r"|\b(?:no|not|without|negative\s+for|free\s+of|absent|denies|denied"
    r"|ruled\s+out|excluded)\b)"
    # …followed only by filler, never across a clause break, up to the phrase.
    r"[^。.;；,，、\n]{0,24}$",
    re.IGNORECASE)


def _is_negated(text: str, start: int) -> bool:
    """Is the phrase starting at ``start`` inside a negation?"""
    return bool(_NEGATION_RE.search(text[max(0, start - 40):start]))


def _needle_re(needle: str) -> re.Pattern:
    """Match a keyword, with word boundaries when it is ASCII.

    Substring matching is why "operable" fired inside "inoperable" (inverting
    resectability) and "nos" fired inside "diagnosis" (inventing a histology).
    CJK needles have no word boundaries and are matched as substrings.
    """
    if needle.isascii():
        return re.compile(r"\b" + re.escape(needle) + r"\b", re.IGNORECASE)
    return re.compile(re.escape(needle))


@functools.lru_cache(maxsize=512)
def _compiled(needle: str) -> re.Pattern:
    return _needle_re(needle)


def _find(text: str, needle: str, *, allow_negated: bool = True):
    """First non-negated occurrence of ``needle``, or None."""
    for m in _compiled(needle).finditer(text):
        if allow_negated or not _is_negated(text, m.start()):
            return m
    return None


def _classify(text: str, table: tuple, *,
              allow_negated: bool = True) -> Optional[str]:
    for value, needles in table:
        if any(_find(text, n, allow_negated=allow_negated) for n in needles):
            return value
    return None


#: Every gene name we recognise. A gene's result window must stop at the next
#: gene: "EGFR 19del positive / ALK negative" has no clause punctuation, so an
#: unbounded window read ALK's "negative" as EGFR's result and recorded an
#: EGFR-mutant patient as wild-type.
_GENE_NAMES = ("EGFR", "ALK", "ROS1", "BRAF", "KRAS", "MET", "RET", "NTRK",
               "HER2", "ERBB2", "PD-L1", "PDL1", "NRG1", "TP53")

#: Clause boundaries in both languages, used to bound a gene's result window.
_CLAUSE_RE = re.compile(r"[。.;；,，、\n]")


def _driver_status(text: str, gene: str) -> Optional[str]:
    """Read one gene's result out of the clause naming it."""
    m = _compiled(gene).search(text)
    if m is None:
        return None
    start = m.end()
    rest = text[start:start + 80]

    # Stop at the first clause break…
    cut = _CLAUSE_RE.search(rest)
    end = cut.start() if cut else len(rest)
    # …or at the next gene named, whichever comes first.
    for other in _GENE_NAMES:
        if other.upper() == gene.upper():
            continue
        found = _compiled(other).search(rest)
        if found and found.start() < end:
            end = found.start()

    window = (m.group(0) + rest[:end]).strip(" \t/|-–—、,，;；:：")
    if any(_find(window, n) for n in _UNTESTED):
        return "not_tested"
    if any(_find(window, n) for n in _NEGATIVE):
        return "negative"
    if any(_find(window, n) for n in _POSITIVE):
        return window or "positive"
    return None


def _canon(value: str, vocab: tuple[str, ...]) -> Optional[str]:
    key = value.replace(" ", "").upper()
    for v in vocab:
        if key == v.upper():
            return v
    return None


def _set_descriptor(values: dict, key: str, raw: str,
                    vocab: tuple[str, ...]) -> None:
    """Store one TNM descriptor, keeping T1mi in its own written form."""
    if raw.lower() == "t1mi":
        values[key] = "T1mi"
        return
    canon = _canon(raw, vocab)
    if canon:
        values[key] = canon


def extract_deterministic(text: str) -> tuple[dict[str, Any], list[str]]:
    """Pull canonical facts out of a reply. Returns (values, notes)."""
    values: dict[str, Any] = {}
    notes: list[str] = []
    if not text or not text.strip():
        return values, notes

    for run in _TNM_RUN_RE.finditer(text):
        if not (_prefix_ok(run.group(1)) and _prefix_ok(run.group(3))):
            continue
        for raw, key, vocab in ((run.group(2), "t_category", T_CATEGORIES),
                                (run.group(4), "n_category", N_CATEGORIES),
                                (run.group(5), "m_category", M_CATEGORIES)):
            if raw:
                _set_descriptor(values, key, raw, vocab)
        break
    for regex, key, vocab in ((_T_RE, "t_category", T_CATEGORIES),
                              (_N_RE, "n_category", N_CATEGORIES),
                              (_M_RE, "m_category", M_CATEGORIES)):
        if key in values:
            continue
        for m in regex.finditer(text):
            if not _prefix_ok(m.group(1)):
                continue
            _set_descriptor(values, key, m.group(2), vocab)
            break

    # Ambiguous descriptors are recorded as a note, never canonicalised: "N2"
    # without a sub-letter is exactly the input the staging engine refuses.
    for m in _AMBIGUOUS_RE.finditer(text):
        token = m.group(1)
        key = {"T1": "t_category", "T2": "t_category", "N2": "n_category",
               "M1": "m_category", "M1c": "m_category"}[token]
        if key not in values:
            notes.append(
                f"AMBIGUOUS_DESCRIPTOR: {token!r} needs its sub-category "
                f"before it can be staged"
            )

    m = _ECOG_RE.search(text)
    if m:
        score = int(m.group(1))
        span = _ECOG_RANGE_RE.match(text, m.end())
        if span:
            upper = int(span.group(1))
            if upper != score:
                notes.append(
                    f"ECOG_RANGE: the reply gave a range ({score}-{upper}); "
                    f"recorded the worse end ({max(score, upper)})."
                )
            score = max(score, upper)
        values["ecog_ps"] = score

    m = _AGE_RE.search(text) or _AGE_COMPACT_RE.search(text)
    if m:
        age = int(m.group(1))
        if 0 < age < 120:
            values["age"] = age

    m = _PDL1_RE.search(text)
    if m:
        tps = int(m.group(1))
        if tps <= 100:
            values["pd_l1"] = tps
    elif _PDL1_NEG_RE.search(text):
        values["pd_l1"] = "<1%"

    # These three are assertions about the patient, so a negated mention
    # ("no tissue diagnosis", "not resectable") must not set them.
    hist = _classify(text, _HISTOLOGY, allow_negated=False)
    if hist:
        values["histology"] = hist

    resect = _classify(text, _RESECTABILITY, allow_negated=False)
    if resect:
        values["resectability"] = resect

    smoke = _classify(text, _SMOKING, allow_negated=False)
    if smoke:
        values["smoking"] = smoke

    for gene, key in (("EGFR", "egfr"), ("ALK", "alk")):
        status = _driver_status(text, gene)
        if status:
            values[key] = status

    stations = _extract_stations(text)
    if stations:
        values["n2_stations"] = stations

    # Only fall back to prose for M when no explicit descriptor was written.
    if "m_category" not in values:
        low = text.lower()
        # Both of these describe findings, so a negated mention rules the
        # finding OUT. Reading "no malignant pleural effusion" as M1a staged a
        # curative patient as IVA and routed them to the metastatic module.
        named_site = next(
            (s for s in _DISTANT_SITES if _find(text, s, allow_negated=False)),
            None,
        )
        m1 = _classify(text, _M1_HINTS, allow_negated=False)
        says_m0 = (any(p.lower() in low for p in _M0_PHRASES)
                   or bool(_M0_WORKUP_RE.search(text)))
        if m1:
            values["m_category"] = m1
        elif named_site and says_m0:
            notes.append(
                f"AMBIGUOUS_DESCRIPTOR: the reply names a metastatic site "
                f"({named_site}) while stating there is no metastasis "
                f"elsewhere — that is M1, but M1b vs M1c1 vs M1c2 depends on "
                f"the lesion count; M not set."
            )
        elif named_site:
            notes.append(
                f"AMBIGUOUS_DESCRIPTOR: distant metastasis described "
                f"({named_site}) but the M sub-category is not stated — "
                f"M1a/M1b/M1c1/M1c2 depends on lesion count and how many "
                f"organ systems are involved."
            )
        elif says_m0:
            values["m_category"] = "M0"

    return values, notes


def _extract_stations(text: str) -> list[str]:
    """IASLC station numbers, read only out of node-related sentences."""
    found: set[str] = set()
    for sentence in _NODE_SENTENCE_RE.findall(text):
        for regex in (_STATION_SIDED_RE, _STATION_WORDED_EN_RE):
            for m in regex.finditer(sentence):
                found.add(m.group(1) + (m.group(2) or "").upper())
        for m in _STATION_WORDED_ZH_RE.finditer(sentence):
            # "共 2 组" / "肿大 2 组" is a count of stations, not station 2.
            if _ZH_COUNT_CUE_RE.search(sentence[max(0, m.start() - 6):
                                                m.start()]):
                continue
            found.add(m.group(1) + (m.group(2) or "").upper())
        # Bare numbers only as standalone list elements ("…, 7 and 10L").
        for chunk in _STATION_SPLIT_RE.split(sentence):
            m = _STATION_BARE_RE.match(chunk.strip())
            if m:
                found.add(m.group(1) + (m.group(2) or "").upper())
    return sorted(found, key=lambda s: (int(re.sub(r"\D", "", s)), s))


# --------------------------------------------------------------------------- #
# Model-assisted extraction                                                   #
# --------------------------------------------------------------------------- #

_EXTRACTION_PROMPT = f"""\
=== {CONSULT_MARKER} (NSCLC consultation) ===

You are a structured-data extractor inside an NSCLC consultation. You are given
the clinician's or patient's free-text reply and a list of fields that are still
unknown. Extract ONLY what the reply actually states.

HARD RULES:
  • Never infer, never fill a field from clinical plausibility, never carry a
    value over from the example. If the reply does not state it, omit the key.
  • Do NOT canonicalise an ambiguous staging descriptor. If the reply says "N2"
    without single- vs multi-station, omit n_category and add a line to "notes".
  • Return null for a field the reply explicitly says is unknown or untested,
    with the reason in "notes".

Respond with ONLY a JSON object:
{{
  "values": {{ "<field key>": <value>, ... }},
  "notes": ["<anything ambiguous or explicitly unknown>"]
}}
"""


def extract_with_model(
    text: str,
    pending_keys: list[str],
    provider: LLMProvider,
    *,
    params: Optional[GenerationParams] = None,
    lang: str = "zh",
) -> tuple[dict[str, Any], list[str]]:
    """Second-pass extraction for the slots the patterns did not fill."""
    if not text.strip() or not pending_keys:
        return {}, []
    fields = "\n".join(
        f"  - {k}: {SLOTS_BY_KEY[k].question('en')}"
        for k in pending_keys if k in SLOTS_BY_KEY
    )
    user = (
        f"Fields still unknown:\n{fields}\n\n"
        f"Reply to read (language: {lang}):\n{text.strip()}"
    )
    p = (params or provider.params).merged(temperature=0.0)
    response = provider.complete(
        [Message("system", _EXTRACTION_PROMPT), Message("user", user)],
        params=p,
    )
    data = _loads(response.content)
    if not isinstance(data, dict):
        return {}, []
    raw = data.get("values")
    notes = [str(n) for n in (data.get("notes") or [])]
    if not isinstance(raw, dict):
        return {}, notes
    values: dict[str, Any] = {}
    for key, value in raw.items():
        if key not in SLOTS_BY_KEY or value in (None, "", [], {}):
            continue
        # The prompt forbids canonicalising an ambiguous descriptor, but a
        # model that ignores it must not get one past the engine either: an
        # unusable "T2" here would make the consultation look complete and
        # then fail at staging time.
        vocab = _DESCRIPTOR_VOCAB.get(key)
        if vocab is not None and not _canon(str(value), vocab):
            notes.append(
                f"MODEL_DESCRIPTOR_REJECTED[{key}]: {value!r} is not a "
                f"9th-edition category — discarded, the question stands."
            )
            continue
        values[key] = value
    return values, notes


#: Slots whose values must be 9th-edition descriptors, checked before a model
#: is allowed to fill them.
_DESCRIPTOR_VOCAB: dict[str, tuple[str, ...]] = {
    "t_category": T_CATEGORIES + ("T1mi",),
    "n_category": N_CATEGORIES,
    "m_category": M_CATEGORIES,
}


_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _loads(text: str) -> Any:
    """Parse a JSON object out of a model reply, tolerating fences and prose."""
    text = (text or "").strip()
    for candidate in _json_candidates(text):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None


def _json_candidates(text: str):
    yield text
    m = _FENCE_RE.search(text)
    if m:
        yield m.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        yield text[start:end + 1]


def extract(
    text: str,
    pending_keys: list[str],
    *,
    provider: Optional[LLMProvider] = None,
    params: Optional[GenerationParams] = None,
    lang: str = "zh",
) -> tuple[dict[str, Any], list[str]]:
    """Full extraction: deterministic patterns, then the model for the rest."""
    values, notes = extract_deterministic(text)
    still_pending = [k for k in pending_keys if k not in values]
    if provider is not None and still_pending:
        try:
            model_values, model_notes = extract_with_model(
                text, still_pending, provider, params=params, lang=lang
            )
        except Exception as exc:  # extraction must never end the consultation
            notes.append(f"EXTRACTION_MODEL_FAILED: {exc}")
        else:
            # Deterministic values win on conflict.
            for k, v in model_values.items():
                values.setdefault(k, v)
            notes.extend(model_notes)
    return values, notes
