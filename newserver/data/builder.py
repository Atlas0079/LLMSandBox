from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..models.components import (
	AgentComponent,
	AgentControlComponent,
	ContainerComponent,
	ContainerSlot,
	CreatureComponent,
	DecisionArbiterComponent,
	LogicControlComponent,
	PlayerControlComponent,
	TagComponent,
	TaskHostComponent,
	UnknownComponent,
	WorkerComponent,
)
from ..models.entity import Entity
from ..models.gametime import GameTime
from ..models.location import Location
from ..models.task import Task
from ..models.world_state import WorldState


@dataclass
class BuildResult:
	world_state: WorldState


def build_world_state(bundle_world: dict[str, Any], entity_templates: dict[str, Any]) -> BuildResult:
	"""
	对齐 Godot `WorldBuilder.gd` 的最小构建逻辑：
	- 创建 Location 并注册
	- 创建 Entity 并注册
	- 将实体 ID 放入 Location.entities_in_location
	- 处理 component_overrides（只做浅覆盖；深逻辑后续补）
	"""

	ws = WorldState()

	world_state_data = bundle_world.get("world_state", {})
	ws.game_time.total_ticks = int(world_state_data.get("current_tick", 0))

	# 1) 先注册地点
	for loc_data in bundle_world.get("locations", []):
		loc_id = str(loc_data.get("location_id", "")).strip()
		if not loc_id:
			continue
		loc = Location(
			location_id=loc_id,
			location_name=str(loc_data.get("location_name", "Unnamed Location")),
			description=str(loc_data.get("description", "")),
		)
		ws.register_location(loc)

	# 2) 创建并注册实体 + 放入地点
	# 记录每个实体的快照与其“声明地点”（用于第二遍 parent_container 修正）
	snapshots_by_entity_id: dict[str, dict[str, Any]] = {}
	declared_location_by_entity_id: dict[str, str] = {}

	for loc_data in bundle_world.get("locations", []):
		loc_id = str(loc_data.get("location_id", "")).strip()
		loc = ws.get_location_by_id(loc_id)
		if loc is None:
			continue

		for snapshot in loc_data.get("entities", []):
			template_id = snapshot.get("template_id")
			instance_id = snapshot.get("instance_id")
			if not template_id or not instance_id:
				continue

			ent = create_entity_from_template(
				template_id=str(template_id),
				instance_id=str(instance_id),
				entity_templates=entity_templates,
			)
			ws.register_entity(ent)
			loc.add_entity_id(ent.entity_id)

			snapshots_by_entity_id[str(ent.entity_id)] = snapshot if isinstance(snapshot, dict) else {}
			declared_location_by_entity_id[str(ent.entity_id)] = loc_id

			overrides = snapshot.get("component_overrides", {}) or {}
			apply_component_overrides(ent, overrides)

	# 2.5) 第二遍：处理 parent_container（建立“收纳关系”，并在必要时修正地点归属）
	for entity_id, snapshot in snapshots_by_entity_id.items():
		if not isinstance(snapshot, dict):
			continue
		parent_id = str(snapshot.get("parent_container", "") or "").strip()
		if not parent_id:
			continue

		child = ws.get_entity_by_id(entity_id)
		if child is None:
			continue

		# parent 可以是实体容器，或地点
		parent_entity = ws.get_entity_by_id(parent_id)
		parent_location = ws.get_location_by_id(parent_id)

		if parent_location is not None:
			# 放入指定地点（仍是 ID-only，不做父子节点）
			_current_move_entity_between_locations(ws, entity_id, parent_location.location_id)
			continue

		if parent_entity is not None:
			cc = parent_entity.get_component("ContainerComponent")
			if not isinstance(cc, ContainerComponent):
				# 容错：若 parent 没有容器组件，创建一个默认容器
				# 用意：允许存档引用先行（LLM/数据驱动可能写了 parent_container 但忘了加 ContainerComponent）
				# 必要性：否则构建阶段会直接失败，世界无法启动
				cc = _create_default_container_component()
				parent_entity.add_component("ContainerComponent", cc)

			cc.add_entity(child)

			# 修正地点归属：child 应属于 parent 所在地点
			parent_loc = ws.get_location_of_entity(parent_entity.entity_id)
			if parent_loc is not None:
				_current_move_entity_between_locations(ws, entity_id, parent_loc.location_id)
			continue

		# 找不到 parent：忽略但不崩溃
		# 假设存在：BuildDiagnostics 记录错误
		# 用意：给 LLM/数据编辑者反馈；必要性：避免静默失败导致难排错
		continue

	# 2.6) 从存档恢复初始任务（tasks）
	for tdata in list(bundle_world.get("tasks", []) or []):
		if not isinstance(tdata, dict):
			continue

		current_agent_id = str(tdata.get("current_agent_id", "") or "").strip()
		target_entity_id = str(tdata.get("target_entity_id", tdata.get("host_entity_id", "")) or "").strip()
		if not target_entity_id:
			continue

		target = ws.get_entity_by_id(target_entity_id)
		if target is None:
			continue

		host_entity = target
		if current_agent_id:
			agent = ws.get_entity_by_id(current_agent_id)
			if agent is not None:
				host_entity = agent

		host = host_entity.get_component("TaskHostComponent")
		if not isinstance(host, TaskHostComponent):
			# 兼容旧名：TaskComponent
			host = host_entity.get_component("TaskComponent")
		if not isinstance(host, TaskHostComponent):
			# 若没有宿主组件，构建时补一个（容错）
			host = TaskHostComponent()
			host_entity.add_component("TaskHostComponent", host)

		task_kwargs: dict[str, Any] = {}
		task_id = str(tdata.get("task_id", "") or "").strip()
		if task_id:
			task_kwargs["task_id"] = task_id

		task_kwargs["task_type"] = str(tdata.get("task_type", tdata.get("verb", "")) or "")
		task_kwargs["action_type"] = str(tdata.get("action_type", "Task") or "Task")
		task_kwargs["target_entity_id"] = target_entity_id
		task_kwargs["progress"] = float(tdata.get("progress", 0.0))
		task_kwargs["required_progress"] = float(tdata.get("required_progress", 1.0))
		task_kwargs["multiple_entity"] = bool(tdata.get("multiple_entity", False))
		task_kwargs["task_status"] = str(tdata.get("task_status", "Inactive"))

		assigned = tdata.get("assigned_agent_ids", []) or []
		if isinstance(assigned, list):
			task_kwargs["assigned_agent_ids"] = [str(x) for x in assigned]

		params = tdata.get("parameters", {}) or {}
		if isinstance(params, dict):
			task_kwargs["parameters"] = dict(params)

		ce = tdata.get("completion_effects", []) or []
		if isinstance(ce, list):
			task_kwargs["completion_effects"] = [x for x in ce if isinstance(x, dict)]

		task_kwargs["progressor_id"] = str(tdata.get("progressor_id", "") or "")
		pp = tdata.get("progressor_params", {}) or {}
		if isinstance(pp, dict):
			task_kwargs["progressor_params"] = dict(pp)
		te = tdata.get("tick_effects", []) or []
		if isinstance(te, list):
			task_kwargs["tick_effects"] = [x for x in te if isinstance(x, dict)]

		task = Task(**task_kwargs)
		# 附着到宿主并注册到全局索引
		try:
			host.add_task(task)
		except Exception:
			# 重复ID等情况：忽略但继续构建
			continue

		ws.register_task(task)

		# 可选：恢复某个 agent 的 current_task（如果存档明确指定）
		if current_agent_id:
			agent = ws.get_entity_by_id(current_agent_id)
			if agent is not None:
				worker = agent.get_component("WorkerComponent")
				if isinstance(worker, WorkerComponent):
					worker.assign_task(task.task_id)

	# 3) 最小初始化（例如 Creature current_*）
	for ent in ws.entities.values():
		ent.ensure_initialized()

	return BuildResult(world_state=ws)


def create_entity_from_template(template_id: str, instance_id: str, entity_templates: dict[str, Any]) -> Entity:
	template = entity_templates.get(template_id, {})
	if not isinstance(template, dict) or not template:
		raise ValueError(f"template not found: {template_id}")

	ent = Entity(
		entity_id=instance_id,
		template_id=template_id,
		entity_name=str(template.get("name", "Unnamed Entity")),
	)

	components_data = template.get("components", {}) or {}
	if not isinstance(components_data, dict):
		components_data = {}

	for comp_name, comp_data in components_data.items():
		ent.add_component(comp_name, _build_component(comp_name, comp_data))

	# 若是 agent，默认注入 WorkerComponent（迁移期数据里可能还没声明）
	# 用意：让 IdleRule 可以基于 current_task_id 判断；必要性：你希望“无 current_task 才有决策权”
	if ent.has_tag("agent") and not ent.has_component("WorkerComponent"):
		ent.add_component("WorkerComponent", WorkerComponent())

	return ent


def _build_component(component_name: str, comp_data: Any):
	"""
	把已迁移组件转成 dataclass；其它组件保持 UnknownComponent(dict)。
	"""

	if component_name == "TagComponent":
		tags = list((comp_data or {}).get("tags", []))
		return TagComponent(tags=[str(x) for x in tags])

	if component_name == "CreatureComponent":
		d = comp_data or {}
		return CreatureComponent(
			max_hp=float(d.get("max_hp", 100.0)),
			max_energy=float(d.get("max_energy", 100.0)),
			max_nutrition=float(d.get("max_nutrition", 100.0)),
		)

	if component_name == "AgentComponent":
		d = comp_data or {}
		return AgentComponent(
			agent_name=str(d.get("agent_name", "")),
			personality_summary=str(d.get("personality_summary", "")),
			common_knowledge_summary=str(d.get("common_knowledge_summary", "")),
		)

	# 新名字：AgentControlComponent
	# 兼容旧数据：LLMControlComponent
	if component_name == "AgentControlComponent" or component_name == "LLMControlComponent":
		d = comp_data or {}
		if not isinstance(d, dict):
			d = {}
		return AgentControlComponent(
			enabled=bool(d.get("enabled", True)),
			provider_id=str(d.get("provider_id", "") or ""),
		)

	if component_name == "PlayerControlComponent":
		d = comp_data or {}
		if not isinstance(d, dict):
			d = {}
		return PlayerControlComponent(
			enabled=bool(d.get("enabled", True)),
			provider_id=str(d.get("provider_id", "player") or "player"),
		)

	if component_name == "LogicControlComponent":
		d = comp_data or {}
		if not isinstance(d, dict):
			d = {}
		return LogicControlComponent(
			enabled=bool(d.get("enabled", True)),
			provider_id=str(d.get("provider_id", "logic") or "logic"),
		)

	if component_name == "ContainerComponent":
		d = comp_data or {}
		slots_data = d.get("slots", {}) or {}
		slots: dict[str, ContainerSlot] = {}
		for slot_id, slot_tpl in slots_data.items():
			cfg = dict(slot_tpl or {})
			cfg.setdefault("capacity_volume", 999.0)
			cfg.setdefault("capacity_count", 999)
			cfg.setdefault("accepted_tags", [])
			cfg.setdefault("transparent", False)
			slots[str(slot_id)] = ContainerSlot(config=cfg, items=[])
		return ContainerComponent(slots=slots)

	if component_name == "DecisionArbiterComponent":
		d = comp_data or {}
		if isinstance(d, dict):
			return DecisionArbiterComponent.from_template_data(d)
		return DecisionArbiterComponent.from_template_data({})

	# 兼容旧 Godot 名称：TaskComponent -> Python: TaskHostComponent
	if component_name == "TaskComponent" or component_name == "TaskHostComponent":
		return TaskHostComponent()

	# 未迁移组件（Edible/LLMControl/Perception/DecisionArbiter/TaskComponent/...）
	raw = comp_data if isinstance(comp_data, dict) else {"value": comp_data}
	return UnknownComponent(data=raw)


def apply_component_overrides(entity: Entity, overrides: dict[str, Any]) -> None:
	"""
	MVP 版覆盖策略：如果组件是 UnknownComponent，则直接浅合并 dict；
	已迁移组件（Tag/Creature/Agent/Container）先不做复杂覆盖，避免语义不一致。

	假设存在：组件级 apply_snapshot()
	用意：与 Godot WorldBuilder 的约定一致，让组件自己处理覆盖；
	必要性：避免 builder 耦合组件内部字段，后续要补这个接口。
	"""

	for comp_name, comp_patch in (overrides or {}).items():
		if not isinstance(comp_patch, dict):
			continue

		comp = entity.get_component(comp_name)
		if comp is None:
			continue

		# 1) UnknownComponent：浅合并 data
		if isinstance(comp, UnknownComponent):
			comp.data.update(comp_patch)
			continue

		# 2) ContainerComponent：支持覆盖 slot config / items（用于读档恢复容器内容）
		if isinstance(comp, ContainerComponent):
			slots_patch = comp_patch.get("slots", None)
			if isinstance(slots_patch, dict):
				for slot_id, slot_p in slots_patch.items():
					if not isinstance(slot_p, dict):
						continue
					sid = str(slot_id)
					if sid not in comp.slots:
						comp.slots[sid] = ContainerSlot(config={}, items=[])
					if "config" in slot_p and isinstance(slot_p["config"], dict):
						comp.slots[sid].config.update(dict(slot_p["config"]))
					if "items" in slot_p and isinstance(slot_p["items"], list):
						comp.slots[sid].items = [str(x) for x in slot_p["items"]]
			continue

		# 3) WorkerComponent：覆盖 current_task_id（用于恢复行动权/正在做的事）
		if isinstance(comp, WorkerComponent):
			if "current_task_id" in comp_patch:
				comp.current_task_id = str(comp_patch.get("current_task_id", "") or "")
			continue

		# 4) 其它已迁移组件：对同名字段做浅赋值（不做深层语义）
		for k, v in comp_patch.items():
			if hasattr(comp, k):
				try:
					setattr(comp, k, v)
				except Exception:
					pass


def _create_default_container_component() -> ContainerComponent:
	# 默认一个名为 "main" 的槽位
	cfg = {
		"capacity_volume": 999.0,
		"capacity_count": 999,
		"accepted_tags": [],
		"transparent": False,
	}
	return ContainerComponent(slots={"main": ContainerSlot(config=cfg, items=[])})


def _current_move_entity_between_locations(ws: WorldState, entity_id: str, to_location_id: str) -> None:
	# 从所有地点移除再加入目标地点（容错优先）
	for loc in ws.locations.values():
		if entity_id in loc.entities_in_location and str(loc.location_id) != str(to_location_id):
			loc.remove_entity_id(entity_id)
	ws.ensure_entity_in_location(entity_id, to_location_id)
