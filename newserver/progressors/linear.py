from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _read_number_from_component(component: Any, prop_name: str, default: float = 0.0) -> float:
	"""
	Compatible with two component forms:
	- dataclass: Direct getattr
	- UnknownComponent: Read from component.data
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
	Linear Progressor (Align with your hardcoded task_recipe shape in Godot WorkerComponent):
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

