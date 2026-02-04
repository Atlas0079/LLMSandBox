from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _read_number_from_component(component: Any, prop_name: str, default: float = 0.0) -> float:
	"""
	兼容两种组件形态：
	- dataclass: 直接 getattr
	- UnknownComponent: 从 component.data 读取
	"""
	if component is None:
		return float(default)

	if hasattr(component, "data") and isinstance(getattr(component, "data"), dict):
		val = component.data.get(prop_name, default)
		try:
			return float(val)
		except Exception:
			return float(default)

	val = getattr(component, prop_name, default)
	try:
		return float(val)
	except Exception:
		return float(default)


@dataclass
class LinearProgressor:
	"""
	线性推进器（对齐你 Godot WorkerComponent 里硬编码 task_recipe 的形状）：
	- base_progress_per_tick
	- progress_contributors: [{component, property, multiplier}]
	"""

	progressor_id: str = "Linear"

	def compute_progress_delta(self, ws: Any, agent_id: str, task: Any, ticks: int) -> float:
		params = getattr(task, "progressor_params", {}) or {}

		base = float(params.get("base_progress_per_tick", 1.0))
		contributors = list(params.get("progress_contributors", []) or [])

		agent = ws.get_entity_by_id(agent_id)
		if agent is None:
			return 0.0

		delta = base
		for c in contributors:
			if not isinstance(c, dict):
				continue
			comp = agent.get_component(str(c.get("component", "")))
			prop = str(c.get("property", ""))
			mul = float(c.get("multiplier", 1.0))
			delta += _read_number_from_component(comp, prop, 0.0) * mul

		return delta * float(ticks)

