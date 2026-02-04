from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class InteractionEngine:
	"""
	最小配方引擎（对齐 Godot InteractionEngine.gd）：
	- 通过 verb + target_tags + parameter_match 匹配 recipe
	- 输出 effects 列表与 context
	"""

	recipe_db: dict[str, Any]

	def process_command(self, ws: Any, agent_id: str, command_data: dict[str, Any]) -> dict[str, Any]:
		verb = command_data.get("verb")
		target_id = command_data.get("target_id")
		params = command_data.get("parameters", {}) or {}

		target = ws.get_entity_by_id(str(target_id))
		if target is None:
			return {"status": "failed", "reason": "NO_TARGET", "message": "target entity not found"}

		recipe = self._find_matching_recipe(verb=str(verb), target=target, params=params)
		if not recipe:
			return {"status": "failed", "reason": "NO_RECIPE", "message": "No matching recipe found for this interaction."}

		context = {"agent_id": agent_id, "target_id": str(target_id), "recipe": recipe}

		process_data = recipe.get("process", {}) or {}
		required_progress = float(process_data.get("required_progress", 0))
		if required_progress != 0:
			# 持续任务：不直接执行 outputs，而是创建 Task 交给 WorkerComponent 随 tick 推进
			# 具体推进逻辑在 WorkerComponent.per_tick；完成效果在 WorldExecutor.FinishTask 执行
			return {"status": "success", "effects": [{"effect": "CreateTask"}], "context": context}

		effects = self._expand_dynamic_outputs(ws, target, recipe.get("outputs", []) or [])
		return {"status": "success", "effects": effects, "context": context}

	def _find_matching_recipe(self, verb: str, target: Any, params: dict[str, Any]) -> dict[str, Any] | None:
		for recipe_id, recipe in (self.recipe_db or {}).items():
			if (recipe or {}).get("verb") != verb:
				continue

			required_tags = (recipe or {}).get("target_tags", []) or []
			ok = True
			for tag in required_tags:
				if not target.has_tag(str(tag)):
					ok = False
					break
			if not ok:
				continue

			if "parameter_match" in (recipe or {}):
				pm = recipe.get("parameter_match") or {}
				if pm:
					key = list(pm.keys())[0]
					val = pm[key]
					if params.get(key) != val:
						continue

			result = dict(recipe)
			result["id"] = recipe_id
			return result

		return None

	def _expand_dynamic_outputs(self, ws: Any, target: Any, outputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
		effects: list[dict[str, Any]] = []
		for eff in outputs:
			if "dynamic_outputs_from_component" in eff:
				dyn = eff["dynamic_outputs_from_component"] or {}
				comp_name = dyn.get("component")
				prop_name = dyn.get("property")

				comp = target.get_component(str(comp_name))
				# UnknownComponent/真实组件都可能用 data dict 承载
				val = None
				if hasattr(comp, "data") and isinstance(getattr(comp, "data"), dict):
					val = comp.data.get(str(prop_name))
				else:
					val = getattr(comp, str(prop_name), None)

				if isinstance(val, list):
					effects.extend([x for x in val if isinstance(x, dict)])
			else:
				if isinstance(eff, dict):
					effects.append(eff)
		return effects

