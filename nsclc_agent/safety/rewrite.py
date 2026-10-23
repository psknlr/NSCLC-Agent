"""Content-preservation guard for a model rewrite of a governed reply.

Governed mode composes the reply deterministically; the optional polish
pass may then make it read better — never say something else. v1.4.1
red-team: the only check on the way out was a new dose figure, so a
rewrite that added a drug, an efficacy figure or a trial, swapped the
recommended drug, changed the stage, told the patient to skip the
work-up, or dropped the status line, a red-flag question or the
disclaimer went out as the reply.

:func:`rewrite_changes` compares the rewrite with the text it replaces and
names the first change of substance; the caller keeps the deterministic
text. A rewrite may reorder, join and reword the connective prose. It may
not:

* drop or reword a protected line — a caution or safety-rule finding
  (⚠️ ⛔), a flag (⚑), a work-up gap (→), a caveated KG or prognosis
  pointer (※), "no recommendation yet", the release-status line, the
  patient disclaimer, an open question — or a NOT-RELEASED tag;
* add or remove a drug, a treatment modality or a reference;
* add a number, change a quantity, a threshold, a test result, the number
  of metastases or the stage group, add a TNM descriptor;
* add a directive, a reassurance or a prognosis the original does not
  carry ("可以不做", "直接手术", "skip", "stop", "不用担心");
* grow by more than half again — new content the lexicons do not know.

A false alarm costs nothing (the deterministic text is always correct), so
every check errs toward discarding.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from typing import Callable, Iterable, Optional

__all__ = ["rewrite_changes", "protected_lines"]

#: Lines kept word for word (after NFKC and whitespace folding).
_PROTECTED_RE = re.compile(
    r"^(?:⚠|⛔|⚑|→|※|目前尚不能给出治疗建议|\[[a-z_]+\]\s)|教学/研究用途")
_QUESTION_RE = re.compile(r"^\d+[.、)]\s*(\S.*)$")
_NOT_RELEASED = "NOT RELEASED"
#: How much longer than the original a rewrite may grow.
_GROWTH_RATIO, _GROWTH_SLACK = 1.5, 80

_STAGE_RES = (
    re.compile(r"(?<![A-Za-z0-9])(IV[AB]|III[ABC]|II[AB]|IA[1-3]|I[AB])(?![A-Za-z0-9])"),
    re.compile(r"(?<![A-Za-z0-9])(0|IV|III|II|I)(?=\s*期)"),
    re.compile(r"\bstage\s+(0|IV|III|II|I)(?![A-Za-z0-9])", re.IGNORECASE),
)
_TNM_RE = re.compile(
    r"(?<![A-Za-z0-9])[cpyra]{0,2}((?:T(?:is|[0-4](?:mi|[a-d])?))?(?:N[0-3][a-c]?\d?)?"
    r"(?:M[01][a-c]?\d?)?)(?![A-Za-z0-9])")
_NUMBER_RE = re.compile(r"(?<![\d.])\d+(?:\.\d+)?")
_UNITS = {
    "%": "%", "个月": "month", "月": "month", "month": "month", "months": "month",
    "周": "week", "week": "week", "weeks": "week", "天": "day", "日": "day",
    "day": "day", "days": "day", "年": "year", "year": "year", "years": "year",
    "岁": "age", "小时": "hour", "hour": "hour", "hours": "hour", "分钟": "minute",
    "次": "times", "级": "grade", "cm": "cm", "mm": "mm", "ml": "ml", "mg": "mg",
    "gy": "gy", "g": "g", "周期": "cycle", "疗程": "cycle", "cycle": "cycle",
    "cycles": "cycle", "fraction": "fraction", "fractions": "fraction", "分次": "fraction",
    "线": "line", "line": "line", "lines": "line",
}
_QUANTITY_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(%|个月|周期|疗程|分次|小时|分钟|fractions?|months?|weeks?|days?|"
    r"years?|hours?|cycles?|lines?|[月周天日年岁次级线]|cm|mm|ml|mg|gy|g)(?![a-z])",
    re.IGNORECASE)
_AT_LEAST = r"≥|>=|>|至少|超过|大于|高于|at\s+least|more\s+than|greater\s+than|over|above"
_AT_MOST = r"≤|<=|<|不足|不到|小于|低于|less\s+than|fewer\s+than|below|under|at\s+most"
_THRESHOLD_RES = (
    (re.compile(r"(?:" + _AT_LEAST + r")\s*(\d+(?:\.\d+)?)", re.IGNORECASE), "≥"),
    (re.compile(r"(?:" + _AT_MOST + r")\s*(\d+(?:\.\d+)?)", re.IGNORECASE), "≤"),
    (re.compile(r"(\d+(?:\.\d+)?)\s*%?\s*(?:及以上|以上|or\s+more|or\s+higher)", re.IGNORECASE), "≥"),
    (re.compile(r"(\d+(?:\.\d+)?)\s*%?\s*(?:及以下|以下|or\s+less|or\s+lower)", re.IGNORECASE), "≤"),
)


def _classes(*groups: tuple[str, str]) -> tuple[tuple[str, re.Pattern[str]], ...]:
    return tuple((label, re.compile(pattern, re.IGNORECASE)) for label, pattern in groups)


#: Test results and metastasis counts: a rewrite may say them again, never
#: turn one into the other.
_FINDINGS = _classes(
    ("positive", r"阳性|(?<!未)检出|positive|(?<!not )detected"),
    ("negative", r"阴性|未检出|野生型|无突变|negative|not\s+detected|wild[- ]?type"),
    ("single", r"单发|孤立|寡转移|single|solitary|oligo"),
    ("multiple", r"多发|多处|广泛|multiple|diffuse|disseminated|widespread"),
)
#: Treatment modalities: a rewrite neither adds nor drops one.
_MODALITIES = _classes(
    ("surgery", r"手术|切除|\bsurg|resect|lobectomy|pneumonectomy|segmentectomy|\boperat"),
    ("radiotherapy", r"放疗|放射治疗|放化疗|立体定向|radiother|radiation|chemoradi|\bsbrt\b|"
                     r"\bsabr\b|\bimrt\b|\bccrt\b|\bwbrt\b|\bsrs\b|\brt\b|\dgy\b"),
    ("chemotherapy", r"化疗|含铂|铂类|chemo(?!radi)|platinum|doublet"),
    ("immunotherapy", r"免疫治疗|免疫疗法|免疫检查点|双免|immunother|checkpoint|\bio\b|"
                      r"pd-?l?1\s*(?:抑制剂|单抗|inhibitor|blockade|antibod)"),
    ("targeted", r"靶向|targeted|\btkis?\b"),
    ("palliative", r"姑息|安宁疗护|临终|hospice|palliat|best\s+supportive|最佳支持"),
    ("clinical trial", r"临床试验|入组|clinical\s+trials?|\benrol"),
    ("ablation", r"消融|ablat"),
    ("intervention", r"支架|栓塞|介入|胸腔引流|胸膜固定|stent|emboli|pleurodesis|drainage"),
    ("observation", r"观察|随访|监测|surveillance|observation|watchful|follow-?up|monitor"),
)
#: What a rewrite may never add: each label's count may only go down.
_DIRECTIVES = _classes(
    ("omit", r"可以不|不必|不需要|无需|不用|免做|跳过|省略|省去|不做|不查|\bskip|\bno\s+need\b|"
             r"\bnot\s+(?:necessary|needed|required)\b|\bunnecessary\b|\bneedn'?t\b|"
             r"\b(?:don'?t|do\s+not|does\s+not|doesn'?t)\s+need\b|\bomit"),
    ("stop", r"停用|停药|停止|暂停|中断|中止|放弃|\bstop|\bdiscontinu|\bwithh?old|\bhold\s+off|\bcease"),
    ("shortcut", r"直接(?:手术|开始|用药|服药|化疗|放疗|治疗|切除|进行|做)|立即|立刻|马上|"
                 r"\bstraight\s+(?:to|away)\b|\bdirectly\b|\bright\s+away\b|\bimmediately\b|"
                 r"\bat\s+once\b"),
    ("switch", r"改为|改用|换成|换用|替代|代替|取代|而不是|而非|也可(?:以)?(?:考虑|选择|用)|或可|"
               r"\binstead\b|\brather\s+than\b|\bswitch(?:ing)?\s+to\b|\bin\s+place\s+of\b|"
               r"\balternativ|\bcould\s+also\b|\bmay\s+also\b"),
    ("defer", r"观察即可|先观察|在家观察|居家观察|等等看|推迟|延后|延迟|暂缓|以后再|\bwait|\bdefer|"
              r"\bpostpon|\bdelay"),
    ("sufficient", r"即可|就行|就可以|就好|足够|\benough\b|\bsufficient\b|\bonly\s+need|\bjust\s+need"),
    ("reassurance", r"不用担心|无需担心|不必担心|别担心|不要担心|没什么大碍|没有大碍|无大碍|没关系|"
                    r"不要紧|不严重|没问题|很安全|完全安全|请放心|保证|一定(?:能|会)|"
                    r"\bdon'?t\s+worry\b|\bno\s+need\s+to\s+worry\b|\bnothing\s+to\s+worry\b|"
                    r"\bharmless\b|\bnot\s+serious\b|\bnothing\s+serious\b|\bcompletely\s+safe\b|"
                    r"\bguarantee"),
    ("prognosis", r"预后|生存|存活|治愈|痊愈|根治|\bprognos|\bsurviv|\bcur(?:e|ed|es|able|ative)\b|"
                  r"\blife\s+expectancy\b"),
    ("dosing", r"减量|加量|剂量|减半|加倍|\bdos(?:e|es|ing|age)\b|\bhalve|\bdouble\s+the\b|"
               r"\breduc|\bescalat"),
    ("schedule", r"每天|每日|每周|每月|每\d|每[一二三四五六七八九十两]+[天周月]|\bdaily\b|\bweekly\b|"
                 r"\bevery\b|\btwice\b|\bq[1-4]w\b"),
)


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", str(text or ""))


def _fold(text: str) -> str:
    return re.sub(r"\s+", " ", _norm(text)).strip()


def protected_lines(text: str, keep: Iterable[str] = ()) -> list[str]:
    """The strings of ``text`` a rewrite must keep word for word (folded):
    protected lines, numbered questions, and any ``keep`` string the text
    contains."""
    out: list[str] = []
    for raw in _norm(text).splitlines():
        line = _fold(raw)
        question = _QUESTION_RE.match(line)
        if question:
            out.append(question.group(1))
        elif line and _PROTECTED_RE.search(line):
            out.append(line)
    folded = _fold(text)
    out += [_fold(k) for k in keep if _fold(k) and _fold(k) in folded]
    return list(dict.fromkeys(out))


def _count(text: str, classes) -> Counter[str]:
    return Counter({label: len(regex.findall(text)) for label, regex in classes
                    if regex.search(text)})


def _stages(text: str) -> Counter[str]:
    return Counter(m.group(1).upper() for regex in _STAGE_RES for m in regex.finditer(text))


def _tnm(text: str) -> Counter[str]:
    return Counter(m.group(1) for m in _TNM_RE.finditer(text) if m.group(1))


def _quantities(text: str) -> Counter[str]:
    return Counter(f"{float(m.group(1)):g} {_UNITS[m.group(2).lower()]}"
                   for m in _QUANTITY_RE.finditer(text))


def _thresholds(text: str) -> Counter[str]:
    return Counter(f"{sign}{float(m.group(1)):g}" for regex, sign in _THRESHOLD_RES
                   for m in regex.finditer(text))


def _references(text: str) -> Counter[str]:
    from ..agentic import provenance
    from ..knowledge import trials

    keys: Counter[str] = Counter()
    for shown, key in provenance.references(text):
        keys[trials.resolve_trial_id(shown) or key] += 1
    for trial_id in trials.TRIALS_BY_ID:
        if trial_id.isalpha() and re.search(
                r"(?<![A-Za-z0-9])" + trial_id + r"(?![A-Za-z0-9])", text):
            keys[trial_id] += 1
    return keys


def _drugs(text: str) -> Counter[str]:
    from ..agentic import provenance

    return provenance.drugs(text)


def _compare(what: str, before: Counter[str], after: Counter[str], *,
             removal: bool, swap: bool) -> Optional[str]:
    """The change between two counts of one kind of content, if any: a new
    item, a dropped one (``removal``), or one said more often while another
    is said less (``swap``) — "80%" → "50%" when 50% was already there."""
    added = sorted(set(after) - set(before))
    if added:
        return f"added {what}: {', '.join(added)}"
    dropped = sorted(set(before) - set(after))
    if removal and dropped:
        return f"removed {what}: {', '.join(dropped)}"
    if swap:
        more = sorted(k for k in after if after[k] > before[k])
        fewer = sorted(k for k in before if after[k] < before[k])
        if more and fewer:
            return f"changed {what}: {', '.join(fewer)} → {', '.join(more)}"
    return None


#: (what, extractor, removal, swap) — in reporting order.
_CHECKS: tuple[tuple[str, Callable[[str], Counter[str]], bool, bool], ...] = (
    ("a drug", _drugs, True, True),
    ("a reference", _references, True, True),
    ("the stage", _stages, True, True),
    ("a TNM descriptor", _tnm, False, True),
    ("a quantity", _quantities, False, True),
    ("a threshold", _thresholds, False, True),
    ("a finding", lambda t: _count(t, _FINDINGS), False, True),
    ("a treatment modality", lambda t: _count(t, _MODALITIES), True, False),
    ("a number", lambda t: Counter(f"{float(n):g}" for n in _NUMBER_RE.findall(t)), False, False),
)


def rewrite_changes(original: str, candidate: str, *, keep: Iterable[str] = ()) -> Optional[str]:
    """The first change of substance ``candidate`` makes to ``original``, as
    a short reason, or None when it only rewords. ``keep``: strings (the
    open questions) that must survive word for word wherever they appear."""
    before, after = _norm(original), _norm(candidate)
    folded = _fold(candidate)
    for line in protected_lines(original, keep):
        if line not in folded:
            return f"dropped or reworded a protected line: {line[:60]!r}"
    if folded.count(_NOT_RELEASED) < _fold(original).count(_NOT_RELEASED):
        return "dropped a NOT-RELEASED tag"
    for what, extract, removal, swap in _CHECKS:
        reason = _compare(what, extract(before), extract(after), removal=removal, swap=swap)
        if reason:
            return reason
    more = _count(after, _DIRECTIVES) - _count(before, _DIRECTIVES)
    if more:
        label = sorted(more)[0]
        regex = dict(_DIRECTIVES)[label]
        said = [m.group(0) for m in regex.finditer(after)]
        return f"added a directive ({label}): {said[-1]!r}"
    if len(after) > len(before) * _GROWTH_RATIO + _GROWTH_SLACK:
        return f"grew from {len(before)} to {len(after)} characters"
    return None
