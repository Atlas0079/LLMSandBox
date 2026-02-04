from __future__ import annotations

from typing import Any

from .agent_control import AgentControlComponent
from .logic_control import LogicControlComponent
from .player_control import PlayerControlComponent


def resolve_enabled_controller_component(entity: Any):
	"""
	从实体上解析“启用的控制器组件”。

	返回：
	- (component_name, component_instance) 或 (None, None)

	说明：
	- 兼容旧数据：LLMControlComponent 会在 builder 中被构建为 AgentControlComponent，
	  但 entity.components 的 key 仍可能是 "LLMControlComponent"。
	"""

	if entity is None or not hasattr(entity, "get_component"):
		return (None, None)

	# 优先级：玩家 > LLM/Agent > 纯逻辑（你后续可以按需求调整）
	candidates = [
		("PlayerControlComponent", PlayerControlComponent),
		("AgentControlComponent", AgentControlComponent),
		("LLMControlComponent", AgentControlComponent),
		("LogicControlComponent", LogicControlComponent),
	]

	for name, cls in candidates:
		comp = entity.get_component(name)
		if isinstance(comp, cls) and bool(getattr(comp, "enabled", True)):
			return (name, comp)

	return (None, None)

