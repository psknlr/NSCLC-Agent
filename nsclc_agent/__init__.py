"""NSCLC-Agent v0.2 — an evidence-governed, stage-verified NSCLC agent harness.

The fusion of two designs:

* **NSCLC-Agent v0.1** contributed the deterministic AJCC/UICC 9th-edition
  staging engine, the stage router and the protocol module library — the
  verifiable clinical core.
* **YaoBi-Harness** contributed the agent architecture around it: a control
  plane the model cannot bypass (capability broker, evidence ledger, budgets,
  release-status machine, unconditional critic, record/replay journal) and a
  cognition layer the model drives (planning, ReAct tool loops, active
  enquiry with an independent adequacy judge, an MDT panel).

The model plans, chooses tools, asks questions and drafts reasoning; it never
assigns a stage, never originates a dose, and never clears a rule-detected
safety issue. With no model configured, the whole pipeline runs
deterministically.

Educational / research use only — not a medical device.
"""

from .case import Case
from .conversation import ConsultationSession, TurnResult
from .render import audit, render
from .runner import NSCLCRunner, resume_run
from .staging import (
    StageResult,
    StagingError,
    TNM,
    normalize_stage_group,
    route,
    stage_from_strings,
)
from .state import CaseRunState, EvidenceLevel

__version__ = "0.2.2"

__all__ = [
    "Case",
    "CaseRunState",
    "ConsultationSession",
    "TurnResult",
    "EvidenceLevel",
    "NSCLCRunner",
    "resume_run",
    "render",
    "audit",
    "TNM",
    "StageResult",
    "StagingError",
    "stage_from_strings",
    "normalize_stage_group",
    "route",
    "__version__",
]
