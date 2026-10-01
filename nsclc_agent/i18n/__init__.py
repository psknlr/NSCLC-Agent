"""Display localisation (zh → en) for the web app and the CLI.

The clinical kernel speaks the language it was written in; localisation
happens at the display edge, from ONE dictionary (``en.json``) shared by
this module and the browser (``web/build.py`` copies it next to the app),
so both surfaces translate identically:

1. an exact phrase match wins;
2. otherwise the regex ``rules`` run (word-order changes around numbers:
   "第 3 轮" → "turn 3"), then every known phrase is replaced as a substring
   (longest first, one pass) — template fragments translate piecewise;
3. bilingual kernel strings ("大咯血 / massive hemoptysis") show their
   English half;
4. once no Chinese is left, full-width punctuation is normalised.

Model output is never passed through here: in English the model is asked
to write English (see ``agentic.prompts.LANGUAGE_DIRECTIVE_EN``).
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

LANGUAGES = ("zh", "en")

#: Han characters (punctuation is handled separately).
HAN_RE = re.compile(r"[㐀-鿿豈-﫿]")
_PUNCT = {"：": ": ", "（": " (", "）": ") ", "，": ", ", "、": ", ", "；": "; ",
          "。": ". ", "！": "! ", "？": "? ", "“": '"', "”": '"', "‘": "'", "’": "'",
          "【": "[", "】": "] ", "《": "", "》": "", "～": "~", "　": " "}
_PUNCT_RE = re.compile("|".join(map(re.escape, _PUNCT)))
_SPACES_RE = re.compile(r"[ \t]{2,}")
_SPACE_BEFORE_RE = re.compile(r" +([,.;:!?)\]])")


@lru_cache(maxsize=2)
def _table(lang: str) -> tuple[dict[str, str], re.Pattern | None, list[tuple[re.Pattern, str]]]:
    path = Path(__file__).with_name(f"{lang}.json")
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    phrases = {k: v for k, v in (data.get("phrases") or {}).items() if isinstance(v, str)}
    keys = sorted((k for k in phrases if len(k.strip()) > 1), key=len, reverse=True)
    phrase_re = re.compile("|".join(re.escape(k) for k in keys)) if keys else None
    rules = []
    for source, replacement in data.get("rules") or []:
        try:
            rules.append((re.compile(source),
                          re.sub(r"\$(\d)", r"\\\1", str(replacement))))
        except re.error:
            continue
    return phrases, phrase_re, rules


def english_half(text: str) -> str:
    """The English half of a bilingual kernel string, or the text itself."""
    if " / " in text:
        head, tail = text.split(" / ", 1)
        if HAN_RE.search(head) and not HAN_RE.search(tail):
            return tail.strip()
    if "；" in text:
        tail = text.rsplit("；", 1)[1].strip()
        if tail and not HAN_RE.search(tail) and re.search(r"[A-Za-z]", tail):
            return tail
    return text


def _tidy(text: str) -> str:
    text = _PUNCT_RE.sub(lambda m: _PUNCT[m.group(0)], text)
    lines = [_SPACE_BEFORE_RE.sub(r"\1", _SPACES_RE.sub(" ", line)).rstrip()
             for line in text.split("\n")]
    return "\n".join(lines).strip(" ")


def translate(text: Any, lang: str = "en") -> Any:
    """Translate one display string (non-strings pass through)."""
    if lang != "en" or not isinstance(text, str) or not HAN_RE.search(text):
        return text
    phrases, phrase_re, rules = _table("en")
    if text in phrases:
        return phrases[text]
    stripped = text.strip()
    if stripped in phrases:
        return text.replace(stripped, phrases[stripped])
    out = english_half(text)
    if not HAN_RE.search(out):
        return out
    for pattern, replacement in rules:
        out = pattern.sub(replacement, out)
    if phrase_re is not None:
        out = phrase_re.sub(lambda m: phrases[m.group(0)], out)
    if not HAN_RE.search(out):
        out = _tidy(out)
    return out


def localize(value: Any, lang: str = "en") -> Any:
    """Translate every string inside a JSON-like value."""
    if lang != "en":
        return value
    if isinstance(value, str):
        return translate(value, lang)
    if isinstance(value, list):
        return [localize(v, lang) for v in value]
    if isinstance(value, dict):
        return {k: localize(v, lang) for k, v in value.items()}
    return value
