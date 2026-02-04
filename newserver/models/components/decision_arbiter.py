from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ...sim.interrupt_rules import IdleRule, LowNutritionRule, InterruptResult
from .controller_resolver import resolve_enabled_controller_component


@dataclass
class DecisionArbiterComponent:
	"""
	Align with Godot `DecisionArbiterComponent.gd`:
	- Holds ruleset
	- Calls `check_if_interrupt_is_needed` every tick
	"""

	ruleset: list[Any] = field(default_factory=list)

	def per_tick(self, _ws: Any, _entity_id: str, _ticks_per_minute: int) -> None:
		# Arbiter component usually doesn't need progression, read-only check suffices.
		return

	@staticmethod
	def from_template_data(component_data: dict[str, Any]) -> "DecisionArbiterComponent":
		rules_raw = component_data.get("rules", []) if isinstance(component_data, dict) else []
		ruleset: list[Any] = []

		for rd in rules_raw:
			rule_type = (rd or {}).get("type")
			if rule_type == "Idle":
				ruleset.append(IdleRule(priority=int((rd or {}).get("priority", 999))))
			elif rule_type == "LowNutrition":
				ruleset.append(
					LowNutritionRule(
						priority=int((rd or {}).get("priority", 10)),
						threshold=float((rd or {}).get("threshold", 50)),
					)
				)
			else:
				# Unmigrated rules: Ignore
				# Assuming existence: UnknownInterruptRule
				# Intent: Keep unknown rule data for debugging; Necessity: Facilitates gradual migration as rule types increase.
				continue

		# Check lower priority first (consistent with your Godot version)
		ruleset.sort(key=lambda r: int(getattr(r, "priority", 999999)))
		return DecisionArbiterComponent(ruleset=ruleset)

	def check_if_interrupt_is_needed(self, ws: Any, agent_id: str) -> InterruptResult:
		# If the entity has no "available controller", do not enter decision (avoid hardcoding control methods in Manager/Arbiter).
		agent = ws.get_entity_by_id(agent_id) if hasattr(ws, "get_entity_by_id") else None
		_ctrl_name, ctrl = resolve_enabled_controller_component(agent)
		if ctrl is None:
			return InterruptResult(interrupt=False, reason="", rule_type="", priority=999999)

		for rule in self.ruleset:
			result = rule.should_interrupt(ws, agent_id)
			if result.interrupt:
				return result
		return InterruptResult(interrupt=False, reason="", rule_type="", priority=999999)

