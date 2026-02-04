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
	Minimal build logic aligned with Godot `WorldBuilder.gd`:
	- Create and register Location
	- Create and register Entity
	- Put entity ID into Location.entities_in_location
	- Handle component_overrides (Shallow override only; deep logic later)
	"""

	ws = WorldState()

	world_state_data = bundle_world.get("world_state", {})
	ws.game_time.total_ticks = int(world_state_data.get("current_tick", 0))

	# 1) Register locations first
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

	# 2) Create and register entities + Put in location
	# Record snapshot and "declared location" for each entity (For 2nd pass parent_container correction)
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

	# 2.5) 2nd Pass: Handle parent_container (Establish "containment", correct location ownership if needed)
	for entity_id, snapshot in snapshots_by_entity_id.items():
		if not isinstance(snapshot, dict):
			continue
		parent_id = str(snapshot.get("parent_container", "") or "").strip()
		if not parent_id:
			continue

		child = ws.get_entity_by_id(entity_id)
		if child is None:
			continue

		# parent can be entity container, or location
		parent_entity = ws.get_entity_by_id(parent_id)
		parent_location = ws.get_location_by_id(parent_id)

		if parent_location is not None:
			# Put in specific location (Still ID-only, no parent-child node)
			_current_move_entity_between_locations(ws, entity_id, parent_location.location_id)
			continue

		if parent_entity is not None:
			cc = parent_entity.get_component("ContainerComponent")
			if not isinstance(cc, ContainerComponent):
				# Fault Tolerance: If parent has no container component, create a default one
				# Intent: Allow archive reference ahead (LLM/Data driven might write parent_container but forgot ContainerComponent)
				# Necessity: Otherwise build phase fails directly, world cannot start
				cc = _create_default_container_component()
				parent_entity.add_component("ContainerComponent", cc)

			cc.add_entity(child)

			# Correct location ownership: child should belong to parent's location
			parent_loc = ws.get_location_of_entity(parent_entity.entity_id)
			if parent_loc is not None:
				_current_move_entity_between_locations(ws, entity_id, parent_loc.location_id)
			continue

		# Parent not found: Ignore but don't crash
		# Assume existence: BuildDiagnostics logs error
		# Intent: Feedback to LLM/Data editor; Necessity: Avoid silent failure making debugging hard
		continue

	# 2.6) Restore initial tasks from archive
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
			# Compatible with old name: TaskComponent
			host = host_entity.get_component("TaskComponent")
		if not isinstance(host, TaskHostComponent):
			# If no host component, add one during build (Fault tolerance)
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
		# Attach to host and register to global index
		try:
			host.add_task(task)
		except Exception:
			# Duplicate ID etc: Ignore but continue building
			continue

		ws.register_task(task)

		# Optional: Restore an agent's current_task (If archive explicitly specifies)
		if current_agent_id:
			agent = ws.get_entity_by_id(current_agent_id)
			if agent is not None:
				worker = agent.get_component("WorkerComponent")
				if isinstance(worker, WorkerComponent):
					worker.assign_task(task.task_id)

	# 3) Minimal initialization (e.g. Creature current_*)
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

	# If agent, inject WorkerComponent by default (Migration data might not declare yet)
	# Intent: Let IdleRule judge based on current_task_id; Necessity: You want "Decision rights only when no current_task"
	if ent.has_tag("agent") and not ent.has_component("WorkerComponent"):
		ent.add_component("WorkerComponent", WorkerComponent())

	return ent


def _build_component(component_name: str, comp_data: Any):
	"""
	Convert migrated components to dataclass; others remain UnknownComponent(dict).
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

	# New name: AgentControlComponent
	# Compatible with old data: LLMControlComponent
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

	# Compatible with old Godot name: TaskComponent -> Python: TaskHostComponent
	if component_name == "TaskComponent" or component_name == "TaskHostComponent":
		return TaskHostComponent()

	# Unmigrated components (Edible/LLMControl/Perception/DecisionArbiter/TaskComponent/...)
	raw = comp_data if isinstance(comp_data, dict) else {"value": comp_data}
	return UnknownComponent(data=raw)


def apply_component_overrides(entity: Entity, overrides: dict[str, Any]) -> None:
	"""
	MVP Override Strategy: If component is UnknownComponent, shallow merge dict directly;
	Migrated components (Tag/Creature/Agent/Container) do not do complex override first, avoid semantic inconsistency.

	Assume existence: Component level apply_snapshot()
	Intent: Consistent with Godot WorldBuilder convention, let component handle override itself;
	Necessity: Avoid builder coupling component internal fields, need to add this interface later.
	"""

	for comp_name, comp_patch in (overrides or {}).items():
		if not isinstance(comp_patch, dict):
			continue

		comp = entity.get_component(comp_name)
		if comp is None:
			continue

		# 1) UnknownComponent: Shallow merge data
		if isinstance(comp, UnknownComponent):
			comp.data.update(comp_patch)
			continue

		# 2) ContainerComponent: Support overriding slot config / items (For restoring container content from archive)
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

		# 3) WorkerComponent: Override current_task_id (For restoring action rights / what is being done)
		if isinstance(comp, WorkerComponent):
			if "current_task_id" in comp_patch:
				comp.current_task_id = str(comp_patch.get("current_task_id", "") or "")
			continue

		# 4) Other migrated components: Shallow assignment to same-name fields (No deep semantics)
		for k, v in comp_patch.items():
			if hasattr(comp, k):
				try:
					setattr(comp, k, v)
				except Exception:
					pass


def _create_default_container_component() -> ContainerComponent:
	# Default slot named "main"
	cfg = {
		"capacity_volume": 999.0,
		"capacity_count": 999,
		"accepted_tags": [],
		"transparent": False,
	}
	return ContainerComponent(slots={"main": ContainerSlot(config=cfg, items=[])})


def _current_move_entity_between_locations(ws: WorldState, entity_id: str, to_location_id: str) -> None:
	# Remove from all locations then add to target location (Fault tolerance priority)
	for loc in ws.locations.values():
		if entity_id in loc.entities_in_location and str(loc.location_id) != str(to_location_id):
			loc.remove_entity_id(entity_id)
	ws.ensure_entity_in_location(entity_id, to_location_id)
