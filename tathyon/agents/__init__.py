"""Five bounded TATHYON agents. See base.py for the guarantees they share."""
from __future__ import annotations

from typing import Optional

from ..workspace import Workspace
from .analyst import ResilienceAnalyst
from .base import AI_LABEL, BoundedAgent, LLMClient
from .copilot import OpsCopilot
from .evidence import EvidenceAgent
from .intake import IntakeAgent
from .llm import default_llm, default_vision, gemini_status
from .replan_watcher import ReplanWatcher

AGENT_CLASSES = {
    "intake_agent": IntakeAgent,
    "ops_copilot": OpsCopilot,
    "resilience_analyst": ResilienceAnalyst,
    "replan_watcher": ReplanWatcher,
    "evidence_agent": EvidenceAgent,
}


def build_agent(name: str, ws: Workspace, llm: Optional[LLMClient] = None) -> BoundedAgent:
    cls = AGENT_CLASSES[name]
    if cls is IntakeAgent:
        return IntakeAgent(ws, vision=default_vision())
    return cls(ws, llm if llm is not None else default_llm())


__all__ = ["AGENT_CLASSES", "AI_LABEL", "build_agent", "gemini_status"]
