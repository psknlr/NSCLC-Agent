"""Protocol module loader with sectioned retrieval.

Two changes over v0.1:

* **Modules are a knowledge source, not a prompt payload.** The full v3.3
  protocol files (40–77k chars) are indexed into titled sections and served
  through the ``protocol_lookup`` tool on demand. The system prompt carries
  only the distilled decision core (:mod:`.cores`), so the prompt fits and the
  numbers the model uses are the ones it *retrieved*, which the ledger records.
* **Provenance.** Each module records its sha256 and declared version at load,
  so a run's audit trail pins exactly which protocol text was in force.
"""

from __future__ import annotations

import functools
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..staging import StagingError, normalize_stage_group, route
from .cores import MIN_OUTPUT_TOKENS, STAGE_CORES

_PROMPT_DIR = Path(__file__).resolve().parent

# module key -> (filename, human label, stage groups covered)
MODULES: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "stage0": ("stage0.md", "Stage 0 (Tis / AIS-MIA)", ("0",)),
    "stage1": ("stage1.md", "Stage I (early-stage curative intent)",
               ("IA1", "IA2", "IA3", "IB")),
    "stage2": ("stage2.md", "Stage II (resectable node-positive, curative)",
               ("IIA", "IIB")),
    "stage3a": ("stage3a.md", "Stage IIIA (resectable / perioperative-adjuvant)",
                ("IIIA",)),
    "stage3b": ("stage3b.md", "Stage IIIB (locally advanced)", ("IIIB",)),
    "stage3c": ("stage3c.md", "Stage IIIC (N3 locally advanced)", ("IIIC",)),
    "stage4a": ("stage4a.md", "Stage IVA (M1a/M1b metastatic)", ("IVA",)),
    "stage4b": ("stage4b.md", "Stage IVB (M1c1/M1c2 polymetastatic)", ("IVB",)),
    "workup": ("workup.md", "Unstaged / occult disease workup", ("Occult",)),
}

_BANNER_RE = re.compile(r"^={8,}\s*$", re.MULTILINE)
_MAX_SECTION_CHARS = 4000
_MAX_SECTIONS_RETURNED = 4


@dataclass
class Section:
    title: str
    text: str

    def to_dict(self) -> dict:
        return {"title": self.title, "text": self.text[:_MAX_SECTION_CHARS]}


@dataclass
class PromptModule:
    key: str
    label: str
    stage_groups: tuple[str, ...]
    path: Path
    full_text: str
    #: Distilled decision core — what actually enters the system prompt.
    core: str
    sha256: str
    min_output_tokens: int
    sections: list[Section] = field(default_factory=list)


class PromptNotFound(FileNotFoundError):
    pass


def _split_sections(text: str) -> list[Section]:
    """Split a module on its ``====`` banner blocks into titled sections."""
    parts = _BANNER_RE.split(text)
    sections: list[Section] = []
    pending_title: Optional[str] = None
    for part in parts:
        chunk = part.strip("\n")
        if not chunk.strip():
            continue
        lines = chunk.strip().splitlines()
        # A short single-line chunk between banners is a section title.
        if len(lines) <= 2 and len(chunk.strip()) < 120:
            pending_title = " ".join(l.strip() for l in lines if l.strip())
            continue
        title = pending_title or (lines[0].strip()[:100] if lines else "(untitled)")
        pending_title = None
        body = chunk.strip()
        # Large section bodies are sub-chunked so retrieval stays bounded.
        while len(body) > _MAX_SECTION_CHARS:
            cut = body.rfind("\n", 0, _MAX_SECTION_CHARS)
            cut = cut if cut > _MAX_SECTION_CHARS // 2 else _MAX_SECTION_CHARS
            sections.append(Section(title, body[:cut]))
            body = body[cut:].lstrip("\n")
            title += " (cont.)"
        sections.append(Section(title, body))
    return sections


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
    return PromptModule(
        key=key,
        label=label,
        stage_groups=groups,
        path=path,
        full_text=text,
        core=STAGE_CORES.get(key, ""),
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        min_output_tokens=MIN_OUTPUT_TOKENS.get(key, 2000),
        sections=_split_sections(text),
    )


def list_modules() -> list[PromptModule]:
    return [load_module(k) for k in MODULES]


def resolve_module_key(stage_or_key: str) -> Optional[str]:
    """Accept a module key ('stage3b') or a stage group ('IIIB' / 'Stage IIIB')."""
    raw = str(stage_or_key).strip()
    if raw in MODULES:
        return raw
    try:
        group = normalize_stage_group(raw)
    except StagingError:
        return None
    result = route(group)
    return result.module_key


def find_sections(stage_or_key: str, query: str = "") -> tuple[list[dict], Optional[str]]:
    """Retrieve protocol sections for a stage/module, ranked by query terms.

    With no query, returns the module's opening sections. Returned payloads are
    bounded in count and size so the observation stays model-consumable.
    """
    key = resolve_module_key(stage_or_key)
    if key is None:
        return [], None
    module = load_module(key)
    terms = [t for t in str(query).lower().split() if t]
    if not terms:
        chosen = module.sections[:_MAX_SECTIONS_RETURNED]
    else:
        scored = []
        for index, section in enumerate(module.sections):
            haystack = (section.title + "\n" + section.text).lower()
            score = sum(haystack.count(term) for term in terms)
            if score:
                scored.append((score, index, section))
        scored.sort(key=lambda item: (-item[0], item[1]))
        chosen = [section for _, _, section in scored[:_MAX_SECTIONS_RETURNED]]
        if not chosen:
            chosen = module.sections[:2]
    return [section.to_dict() for section in chosen], key
