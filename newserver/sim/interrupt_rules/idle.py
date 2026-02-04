from __future__ import annotations

from dataclasses import dataclass

from .base import InterruptResult


@dataclass
class IdleRule:
	"""
	Align with Godot `RuleIdle.gd` semantics:
	- If agent has no current_task (or no WorkerComponent), grant decision rights.
	"""

	priority: int = 999

	def should_interrupt(self, ws: object, agent_id: str) -> InterruptResult:
		agent = ws.get_entity_by_id(agent_id) if hasattr(ws, "get_entity_by_id") else None
		worker = agent.get_component("WorkerComponent") if agent is not None else None

		# If no WorkerComponent, treat as "No Task", interruptible (Idle)
		has_task = False
		if worker is not None:
			cur = getattr(worker, "current_task_id", "")
			has_task = bool(cur)

		if not has_task:
			return InterruptResult(
				interrupt=True,
				reason="Idle state",
				rule_type="Idle",
				priority=self.priority,
			)

		return InterruptResult(interrupt=False, reason="", rule_type="Idle", priority=self.priority)

