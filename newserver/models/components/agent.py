from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AgentComponent:
	agent_name: str = ""
	personality_summary: str = ""
	common_knowledge_summary: str = ""

