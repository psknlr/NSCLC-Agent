"""Cognition layer: agents whose model-driven bodies are contained by the
control plane, each with a deterministic fallback."""

from .catalog import (
    DosePlanAgent,
    EmergencyAgent,
    IntakeAgent,
    InterviewAgent,
    PerceptionAgent,
    StagingAgent,
    TreatmentAgent,
    deterministic_plan,
)
from .critic import CriticAgent
from .panel import PanelAgent
from .planner import AGENT_CATALOG, PlannerAgent, default_plan, parse_plan, validate_plan
from .toolloop import ToolLoop, ToolLoopResult

__all__ = [
    "IntakeAgent", "EmergencyAgent", "InterviewAgent", "PerceptionAgent",
    "StagingAgent", "TreatmentAgent", "DosePlanAgent", "PanelAgent",
    "CriticAgent", "PlannerAgent", "AGENT_CATALOG", "default_plan",
    "parse_plan", "validate_plan", "deterministic_plan",
    "ToolLoop", "ToolLoopResult",
]
