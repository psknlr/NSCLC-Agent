"""Loader for the stage-specific NSCLC protocol modules.

Each module is a self-contained Markdown system prompt that encodes the
evidence-based decision framework, JSON output schema and safety rules for one
stage band. The Markdown *is* the protocol; this loader just reads it and
exposes light metadata, including the version banner parsed from the file
itself so the shipped version is never asserted from a stale docstring.
"""

from __future__ import annotations

import functools
import re
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

_PROMPT_DIR = Path(__file__).resolve().parent

# module key -> (filename, human label, stage groups covered)
MODULES: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "stage1": ("stage1.md", "Stage I (early-stage curative intent)",
               ("0", "IA1", "IA2", "IA3", "IB")),
    "stage2": ("stage2.md", "Stage II (resectable node-positive, curative)",
               ("IIA", "IIB")),
    "stage3a": ("stage3a.md",
                "Stage IIIA (resectable / perioperative-adjuvant)", ("IIIA",)),
    "stage3b": ("stage3b.md", "Stage IIIB (locally advanced)", ("IIIB",)),
    "stage3c": ("stage3c.md", "Stage IIIC (N3 locally advanced)", ("IIIC",)),
    "stage4a": ("stage4a.md", "Stage IVA (M1a/M1b metastatic)", ("IVA",)),
    "stage4b": ("stage4b.md", "Stage IVB (M1c1/M1c2 polymetastatic)", ("IVB",)),
}


_VERSION_RE = re.compile(r"^\s*Version\s+(.+?)\s*$", re.MULTILINE)


@dataclass
class PromptModule:
    key: str
    label: str
    stage_groups: tuple[str, ...]
    path: Path
    system_prompt: str
    #: version banner as written in the Markdown itself (never hard-coded)
    version: Optional[str] = None


class PromptNotFound(FileNotFoundError):
    pass


@functools.lru_cache(maxsize=None)
def load_module(key: str) -> PromptModule:
    """Load and cache a protocol module by key (e.g. 'stage3b')."""
    if key not in MODULES:
        raise PromptNotFound(
            f"Unknown module {key!r}. Available: {', '.join(sorted(MODULES))}"
        )
    filename, label, groups = MODULES[key]
    path = _PROMPT_DIR / filename
    if not path.is_file():
        raise PromptNotFound(f"Protocol file missing: {path}")
    text = path.read_text(encoding="utf-8")
    m = _VERSION_RE.search(text[:2000])
    return PromptModule(key, label, groups, path, text,
                        version=m.group(1) if m else None)


def list_modules() -> list[PromptModule]:
    return [load_module(k) for k in MODULES]
