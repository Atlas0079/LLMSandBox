from __future__ import annotations

from dataclasses import dataclass

from .base import InterruptResult
from ...models.components import CreatureComponent


@dataclass
class LowNutritionRule:
	"""
	对齐 Godot `RuleLowNutrition.gd`：营养低于阈值时触发中断。
	"""

	priority: int = 10
	threshold: float = 50.0

	def should_interrupt(self, ws: object, agent_id: str) -> InterruptResult:
		agent = ws.get_entity_by_id(agent_id)
		if agent is None:
			return InterruptResult(interrupt=False, rule_type="LowNutrition", priority=self.priority)

		creature = agent.get_component("CreatureComponent")
		if not isinstance(creature, CreatureComponent):
			return InterruptResult(interrupt=False, rule_type="LowNutrition", priority=self.priority)

		creature.ensure_initialized()
		if creature.current_nutrition is None:
			return InterruptResult(interrupt=False, rule_type="LowNutrition", priority=self.priority)

		if float(creature.current_nutrition) < float(self.threshold):
			return InterruptResult(
				interrupt=True,
				reason="营养过低，需要进食",
				rule_type="LowNutrition",
				priority=self.priority,
			)

		return InterruptResult(interrupt=False, rule_type="LowNutrition", priority=self.priority)

