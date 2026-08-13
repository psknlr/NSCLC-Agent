"""NSCLC Agent — evidence-based, stage-aware decision-support for teaching.

A stage-aware NSCLC decision-support agent built around six ideas:

1. An **autonomous consultation loop** (自主问诊) asks for what is missing,
   ordered by what each answer can still change, and stops when nothing left
   unasked would move the recommendation.
2. A **perception layer** (optional) reads radiology films through a
   vision-capable backend (e.g. Gemini via the Poe API) and *proposes* candidate
   radiographic TNM descriptors — it never assigns the stage group.
3. A **deterministic AJCC/UICC 9th-edition staging engine** computes the stage
   group symbolically from the verified descriptors (the model never invents it).
4. A **stage router** dispatches each case to the matching evidence-based
   protocol module (Stage I / II / IIIA / IIIB / IIIC / IVA / IVB).
5. An **evidence layer** retrieves the literature it cites, or records
   explicitly that it did not — so a citation is never silently model recall.
6. A **pluggable provider layer** runs the reasoning on any of LiteLLM, Azure
   OpenAI, Poe or MiniMax (plus an offline mock) for teaching and testing.

For educational / research use only — not a medical device.
"""

from .agent import AgentResult, NSCLCAgent
from .case import Case
from .config import Config, load_config
from .consult import ConsultSession, next_questions, rank_slots
from .evidence import EvidenceRecord, build_retriever
from .imaging import ImagingFindings, ImagingReader, load_image_ref
from .staging import normalize_stage_group, route, stage_from_strings

__version__ = "0.2.0"

__all__ = [
    "NSCLCAgent",
    "AgentResult",
    "Case",
    "Config",
    "load_config",
    "ConsultSession",
    "next_questions",
    "rank_slots",
    "EvidenceRecord",
    "build_retriever",
    "ImagingFindings",
    "ImagingReader",
    "load_image_ref",
    "stage_from_strings",
    "normalize_stage_group",
    "route",
    "__version__",
]
