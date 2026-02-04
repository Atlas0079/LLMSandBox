from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .entity import Entity
from .gametime import GameTime
from .location import Location
from .components import ContainerComponent
from .task import Task


@dataclass
class WorldState:
	"""
	后端世界状态的唯一真相（Single Source of Truth）。
	对齐 Godot 的 `WorldManager.gd` 的核心索引能力，但这里是纯数据结构。
	"""

	game_time: GameTime = field(default_factory=GameTime)

	entities: dict[str, Entity] = field(default_factory=dict)
	locations: dict[str, Location] = field(default_factory=dict)
	tasks: dict[str, Task] = field(default_factory=dict)

	paths: dict[str, Any] = field(default_factory=dict)

	# 运行期服务注册表（由 WorldManager 注入，供组件在 per_tick 中访问系统能力）
	# 约定 key（可扩展）：
	# - "perception_system"
	# - "interaction_engine"
	# - "default_action_provider"
	# - "action_providers"
	services: dict[str, Any] = field(default_factory=dict)

	# per_tick 阶段由组件写入，Manager 统一消费（执行器唯一写入口）
	# item: {"effect": {...}, "context": {...}}
	pending_effects: list[dict[str, Any]] = field(default_factory=list)

	# 世界事件日志（用于观测/回放/调试）
	# 约定：每条记录包含 tick + 发生地点（用于“同地点可见”的过滤）
	event_log: list[dict[str, Any]] = field(default_factory=list)
	_event_seq: int = 0

	# 交互/配方级日志（给 LLM/Planner 用的“可读事件流”的结构化来源）
	# 说明：
	# - 记录每次 ActionAttempt（成功/失败都记）
	# - 记录必要的“名字快照”，避免实体销毁后无法渲染叙事
	interaction_log: list[dict[str, Any]] = field(default_factory=list)
	_interaction_seq: int = 0

	def record_interaction_attempt(
		self,
		actor_id: str,
		verb: str,
		target_id: str,
		status: str,
		reason: str = "",
		recipe_id: str = "",
	) -> None:
		"""
		记录一次动作尝试（ActionAttempt / InteractionAttempt）。

		约定字段：
		- actor_id/target_id/verb/recipe_id/status/reason
		- actor_name/target_name：名字快照（用于多视角渲染与实体销毁后的回放）
		- location_id：发生地点快照（用于“同地点可见”过滤）
		"""

		aid = str(actor_id or "")
		tid = str(target_id or "")
		v = str(verb or "")
		st = str(status or "")
		rs = str(reason or "")
		rid = str(recipe_id or "")

		actor = self.get_entity_by_id(aid) if aid else None
		target = self.get_entity_by_id(tid) if tid else None

		actor_name = str(getattr(actor, "entity_name", "") or aid)
		target_name = str(getattr(target, "entity_name", "") or tid)

		loc_id = ""
		if aid:
			loc = self.get_location_of_entity(aid)
			if loc is not None:
				loc_id = str(getattr(loc, "location_id", "") or "")

		self._interaction_seq += 1
		self.interaction_log.append(
			{
				"seq": int(self._interaction_seq),
				"tick": int(getattr(self.game_time, "total_ticks", 0)),
				"location_id": loc_id,
				"actor_id": aid,
				"actor_name": actor_name,
				"verb": v,
				"target_id": tid,
				"target_name": target_name,
				"recipe_id": rid,
				"status": st,
				"reason": rs,
			}
		)

	def record_event(self, event: dict[str, Any], context: dict[str, Any] | None = None) -> None:
		"""
		记录一条世界事件到 event_log。

		说明：
		- event 是执行器返回的事件 dict（例如 PropertyModified/EntityDestroyed/TaskFinished）
		- context 主要用于补充 actor_id（通常是 agent_id）与地点信息
		"""

		if not isinstance(event, dict):
			return

		ctx = context or {}
		actor_id = str(ctx.get("agent_id", "") or ctx.get("actor_id", "") or "")

		loc_id = ""
		if actor_id:
			loc = self.get_location_of_entity(actor_id)
			if loc is not None:
				loc_id = str(getattr(loc, "location_id", "") or "")

		self._event_seq += 1
		self.event_log.append(
			{
				"seq": int(self._event_seq),
				"tick": int(getattr(self.game_time, "total_ticks", 0)),
				"location_id": loc_id,
				"actor_id": actor_id,
				"event": dict(event),
			}
		)

	def register_entity(self, entity: Entity) -> None:
		if entity.entity_id in self.entities:
			raise ValueError(f"entity id already exists: {entity.entity_id}")
		self.entities[entity.entity_id] = entity

	def register_location(self, location: Location) -> None:
		if location.location_id in self.locations:
			raise ValueError(f"location id already exists: {location.location_id}")
		self.locations[location.location_id] = location

	def register_task(self, task: Task) -> None:
		if task.task_id in self.tasks:
			raise ValueError(f"task id already exists: {task.task_id}")
		self.tasks[task.task_id] = task

	def get_entity_by_id(self, entity_id: str) -> Entity | None:
		return self.entities.get(entity_id)

	def get_location_by_id(self, location_id: str) -> Location | None:
		return self.locations.get(location_id)

	def get_task_by_id(self, task_id: str) -> Task | None:
		return self.tasks.get(task_id)

	def unregister_task(self, task_id: str) -> None:
		self.tasks.pop(task_id, None)

	# --- 位置解析（对齐 Godot WorldManager.get_location_of_entity）---
	def get_location_of_entity(self, entity_id: str) -> Location | None:
		visited: set[str] = set()
		return self._resolve_location_for_entity(entity_id, visited)

	def _resolve_location_for_entity(self, entity_id: str, visited: set[str]) -> Location | None:
		if not entity_id:
			return None
		if entity_id in visited:
			return None
		visited.add(entity_id)

		# 1) 直接在地点列表中查找
		for loc in self.locations.values():
			if entity_id in loc.entities_in_location:
				return loc

		# 2) 若不在地点，尝试查找其直接容器实体
		parent_container = self._find_container_entity_holding_item(entity_id)
		if parent_container is not None:
			return self._resolve_location_for_entity(parent_container.entity_id, visited)

		return None

	def _find_container_entity_holding_item(self, item_id: str) -> Entity | None:
		for ent in self.entities.values():
			comp = ent.get_component("ContainerComponent")
			if isinstance(comp, ContainerComponent):
				if item_id in comp.get_all_item_ids():
					return ent
		return None

	# --- 地点索引维护（对齐 Godot WorldManager.ensure_entity_in_location / removed）---
	def ensure_entity_in_location(self, entity_id: str, location_id: str) -> None:
		loc = self.get_location_by_id(location_id)
		if loc is None:
			return
		if entity_id not in loc.entities_in_location:
			loc.entities_in_location.append(entity_id)

	def ensure_entity_removed_from_location(self, entity_id: str, location_id: str) -> None:
		loc = self.get_location_by_id(location_id)
		if loc is None:
			return
		if entity_id in loc.entities_in_location:
			loc.entities_in_location.remove(entity_id)

	def move_ids_between_locations(self, ids: list[str], from_location_id: str, to_location_id: str) -> None:
		for eid in ids:
			self.ensure_entity_removed_from_location(eid, from_location_id)
			self.ensure_entity_in_location(eid, to_location_id)

	def collect_descendant_item_ids(self, root_entity_id: str) -> list[str]:
		"""
		递归收集容器实体的后代物品 ID（对齐 Godot collect_descendant_item_ids）。
		"""
		collected: list[str] = []
		root = self.get_entity_by_id(root_entity_id)
		if root is None:
			return collected
		comp = root.get_component("ContainerComponent")
		if not isinstance(comp, ContainerComponent):
			return collected
		for child_id in comp.get_all_item_ids():
			collected.append(child_id)
			collected.extend(self.collect_descendant_item_ids(child_id))
		return collected

