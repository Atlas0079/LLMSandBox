from __future__ import annotations

from dataclasses import dataclass

from .base import InterruptResult


@dataclass
class IdleRule:
	"""
	对齐 Godot `RuleIdle.gd` 的语义：
	- 若 agent 没有 current_task（或没有 WorkerComponent），则给予决策权
	"""

	priority: int = 999

	def should_interrupt(self, ws: object, agent_id: str) -> InterruptResult:
		agent = ws.get_entity_by_id(agent_id) if hasattr(ws, "get_entity_by_id") else None
		worker = agent.get_component("WorkerComponent") if agent is not None else None

		# 若无 WorkerComponent，按“无任务”处理为可中断（空闲）
		has_task = False
		if worker is not None:
			cur = getattr(worker, "current_task_id", "")
			has_task = bool(cur)

		if not has_task:
			return InterruptResult(
				interrupt=True,
				reason="处于空闲状态",
				rule_type="Idle",
				priority=self.priority,
			)

		return InterruptResult(interrupt=False, reason="", rule_type="Idle", priority=self.priority)

