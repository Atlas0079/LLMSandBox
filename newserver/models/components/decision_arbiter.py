from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ...sim.interrupt_rules import IdleRule, LowNutritionRule, InterruptResult
from .controller_resolver import resolve_enabled_controller_component


@dataclass
class DecisionArbiterComponent:
	"""
	对齐 Godot `DecisionArbiterComponent.gd`：
	- 持有 ruleset
	- 每 tick 调用 `check_if_interrupt_is_needed`
	"""

	ruleset: list[Any] = field(default_factory=list)

	def per_tick(self, _ws: Any, _entity_id: str, _ticks_per_minute: int) -> None:
		# 仲裁组件通常不需要推进，只读判断即可。
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
				# 未迁移规则：忽略
				# 假设存在：UnknownInterruptRule
				# 用意：保留未知规则数据用于调试；必要性：当规则种类增多时便于逐步迁移
				continue

		# 优先级小的先检查（与你 Godot 版一致）
		ruleset.sort(key=lambda r: int(getattr(r, "priority", 999999)))
		return DecisionArbiterComponent(ruleset=ruleset)

	def check_if_interrupt_is_needed(self, ws: Any, agent_id: str) -> InterruptResult:
		# 若该实体没有“可用控制器”，则不进入决策（避免 Manager/Arbiter 对控制方式写死）
		agent = ws.get_entity_by_id(agent_id) if hasattr(ws, "get_entity_by_id") else None
		_ctrl_name, ctrl = resolve_enabled_controller_component(agent)
		if ctrl is None:
			return InterruptResult(interrupt=False, reason="", rule_type="", priority=999999)

		for rule in self.ruleset:
			result = rule.should_interrupt(ws, agent_id)
			if result.interrupt:
				return result
		return InterruptResult(interrupt=False, reason="", rule_type="", priority=999999)

