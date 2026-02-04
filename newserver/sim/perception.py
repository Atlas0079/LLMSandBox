from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..models.components import ContainerComponent


@dataclass
class PerceptionSystem:
	"""
	感知系统：
	- 给出 agent 所在地点
	- 给出地点内“可见”的实体（含 id/name/tags）

	V2（当前实现）：支持容器可见性
	- 地点（Location）存“空间索引”：entities_in_location
	- 容器（ContainerComponent）存“包含关系”
	- 默认：被收纳的实体不可见
	- 若容器 slot.config.transparent == true：可见该 slot 内的实体，并递归展开其透明内容
	"""

	def get_visible_events(self, ws: Any, agent_id: str, max_events: int = 20, tick_window: int = 10) -> list[dict[str, Any]]:
		"""
		观测“动态信息”：返回 agent 最近可见的事件。

		规则（MVP）：
		- 仅返回与 agent 同地点发生的事件（location_id 相同）
		- 仅返回最近 tick_window 个 tick 内的事件
		- 返回最近 max_events 条
		"""

		loc = ws.get_location_of_entity(agent_id)
		if loc is None:
			return []

		loc_id = str(getattr(loc, "location_id", "") or "")
		now_tick = int(getattr(getattr(ws, "game_time", None), "total_ticks", 0))
		min_tick = max(0, now_tick - int(tick_window))

		out: list[dict[str, Any]] = []
		for item in reversed(list(getattr(ws, "event_log", []) or [])):
			if not isinstance(item, dict):
				continue
			if str(item.get("location_id", "") or "") != loc_id:
				continue
			tick = int(item.get("tick", 0) or 0)
			if tick < min_tick:
				break
			# 只暴露最小必要字段给上层（避免把内部结构绑死）
			ev = item.get("event", {})
			if isinstance(ev, dict):
				out.append(
					{
						"tick": tick,
						"actor_id": str(item.get("actor_id", "") or ""),
						"type": str(ev.get("type", "") or ""),
						"event": dict(ev),
					}
				)
			if len(out) >= int(max_events):
				break

		out.reverse()
		return out

	def get_visible_interactions(self, ws: Any, viewer_id: str, max_records: int = 20, tick_window: int = 10) -> list[dict[str, Any]]:
		"""
		观测“可读交互日志”：返回 viewer 最近可见的动作尝试（recipe/interaction 级）。

		规则（MVP）：
		- 仅返回与 viewer 同地点发生的记录（location_id 相同）
		- 仅返回最近 tick_window 个 tick 内的记录
		- 返回最近 max_records 条

		返回结构：
		- [{"tick": int, "text": str, "actor_id": str, "status": str, "reason": str}]
		"""

		loc = ws.get_location_of_entity(viewer_id)
		if loc is None:
			return []

		loc_id = str(getattr(loc, "location_id", "") or "")
		now_tick = int(getattr(getattr(ws, "game_time", None), "total_ticks", 0))
		min_tick = max(0, now_tick - int(tick_window))

		# 从运行期服务拿到 recipe_db（用于模板渲染）
		recipe_db: dict[str, Any] = {}
		services = getattr(ws, "services", {}) or {}
		engine = services.get("interaction_engine")
		if engine is not None and hasattr(engine, "recipe_db"):
			try:
				if isinstance(engine.recipe_db, dict):
					recipe_db = engine.recipe_db
			except Exception:
				recipe_db = {}

		out: list[dict[str, Any]] = []
		for item in reversed(list(getattr(ws, "interaction_log", []) or [])):
			if not isinstance(item, dict):
				continue
			if str(item.get("location_id", "") or "") != loc_id:
				continue
			tick = int(item.get("tick", 0) or 0)
			if tick < min_tick:
				break

			text = self._render_interaction_record(item, viewer_id, recipe_db)
			out.append(
				{
					"tick": tick,
					"text": text,
					"actor_id": str(item.get("actor_id", "") or ""),
					"status": str(item.get("status", "") or ""),
					"reason": str(item.get("reason", "") or ""),
				}
			)
			if len(out) >= int(max_records):
				break

		out.reverse()
		return out

	def _render_interaction_record(self, record: dict[str, Any], viewer_id: str, recipe_db: dict[str, Any]) -> str:
		"""
		把一条 interaction_log 记录渲染成自然语言叙述（MVP：仅支持“我/他人名字”两种视角）。
		"""

		actor_id = str(record.get("actor_id", "") or "")
		actor_name = str(record.get("actor_name", "") or actor_id)
		target_name = str(record.get("target_name", "") or str(record.get("target_id", "") or ""))
		verb = str(record.get("verb", "") or "")
		status = str(record.get("status", "") or "")
		reason = str(record.get("reason", "") or "")
		recipe_id = str(record.get("recipe_id", "") or "")

		actor_text = "我" if str(viewer_id) == actor_id else actor_name

		reason_map = {
			"NO_TARGET": "没找到目标",
			"NO_RECIPE": "没有对应交互规则",
			"TASK_NOT_IMPLEMENTED": "持续任务尚未实现",
		}
		reason_text = reason_map.get(reason, reason or "未知原因")

		template = ""
		if recipe_id and isinstance(recipe_db, dict) and recipe_id in recipe_db:
			recipe = recipe_db.get(recipe_id, {}) or {}
			if isinstance(recipe, dict):
				if status == "success":
					template = str(recipe.get("narrative_success", "") or "")
				else:
					template = str(recipe.get("narrative_fail", "") or "")

		if not template:
			if status == "success":
				template = "{actor}执行了{verb}（{target}）"
			else:
				template = "{actor}尝试{verb}（{target}）但失败了：{reason}"

		return (
			str(template)
			.replace("{actor}", actor_text)
			.replace("{target}", target_name)
			.replace("{verb}", verb)
			.replace("{reason}", reason_text)
		)

	def perceive(self, ws: Any, agent_id: str, include_events: bool = False, include_interactions: bool = False) -> dict[str, Any]:
		loc = ws.get_location_of_entity(agent_id)
		if loc is None:
			return {"agent_id": agent_id, "location": None, "entities": []}

		# 1) 先计算“被收纳的实体ID集合”（用于从地点列表中剔除隐藏实体）
		contained_ids = self._collect_contained_ids_in_location(ws, loc.location_id)

		# 2) 顶层可见实体：在地点里但不在任何容器里
		visible_ids: list[str] = []
		for eid in list(loc.entities_in_location):
			if eid not in contained_ids:
				visible_ids.append(str(eid))

		# 3) 递归展开透明容器的可见内容
		visible_ids = self._expand_transparent_contents(ws, visible_ids)

		# 4) 输出可见实体信息
		entities: list[dict[str, Any]] = []
		for eid in visible_ids:
			ent = ws.get_entity_by_id(eid)
			if ent is None:
				continue
			entities.append(
				{
					"id": ent.entity_id,
					"name": ent.entity_name,
					"tags": ent.get_all_tags(),
				}
			)

		result = {
			"agent_id": agent_id,
			"location": {"id": loc.location_id, "name": loc.location_name},
			"entities": entities,
			# 便于调试（前端可不显示）
			"hidden_entity_count": max(0, len(loc.entities_in_location) - len(visible_ids)),
		}
		if include_events:
			result["events"] = self.get_visible_events(ws, agent_id)
		if include_interactions:
			result["interactions"] = self.get_visible_interactions(ws, agent_id)
		return result

	def _collect_contained_ids_in_location(self, ws: Any, location_id: str) -> set[str]:
		"""
		收集“属于某个容器”的实体ID集合（仅限同一地点）。
		用意：用于从地点列表中剔除被收纳实体（不透明默认不可见）。
		必要性：地点索引与容器关系是正交的，不做这步会导致 agent 全知。
		"""
		contained: set[str] = set()

		for ent in list(getattr(ws, "entities", {}).values()):
			if ent is None:
				continue
			# 只统计同地点的容器，避免跨地点容器泄漏
			ent_loc = ws.get_location_of_entity(getattr(ent, "entity_id", ""))
			if ent_loc is None or str(getattr(ent_loc, "location_id", "")) != str(location_id):
				continue

			cc = ent.get_component("ContainerComponent")
			if not isinstance(cc, ContainerComponent):
				continue

			for item_id in cc.get_all_item_ids():
				contained.add(str(item_id))

		return contained

	def _expand_transparent_contents(self, ws: Any, seed_visible_ids: list[str]) -> list[str]:
		"""
		从“顶层可见实体”出发，递归展开所有透明槽位的内容。
		规则：只有当一个容器实体本身可见时，才允许看到其 transparent 槽位内的实体。
		"""
		visible: list[str] = []
		seen: set[str] = set()

		queue: list[str] = []
		for eid in seed_visible_ids:
			s = str(eid)
			if s and s not in seen:
				seen.add(s)
				queue.append(s)

		while queue:
			current_id = queue.pop(0)
			visible.append(current_id)

			ent = ws.get_entity_by_id(current_id)
			if ent is None:
				continue

			cc = ent.get_component("ContainerComponent")
			if not isinstance(cc, ContainerComponent):
				continue

			# 只展开透明槽位
			for slot in cc.slots.values():
				cfg = getattr(slot, "config", {}) or {}
				if not bool(cfg.get("transparent", False)):
					continue
				for item_id in list(getattr(slot, "items", []) or []):
					iid = str(item_id)
					if iid and iid not in seen:
						seen.add(iid)
						queue.append(iid)

		return visible
