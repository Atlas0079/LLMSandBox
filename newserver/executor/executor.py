from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from ..models.components import ContainerComponent, CreatureComponent, TaskHostComponent, WorkerComponent
from ..models.task import Task


@dataclass
class WorldExecutor:
	"""
	Executor: Single entry point for world "write operations" (Align with Godot WorldExecutor.gd).

	Note:
	- This class only concerns "how to write", not "why to write" (Decision logic in Manager/LLM/Policy layer).
	"""

	# Template required when creating entity at runtime; if not provided, CreateEntity will report error event
	entity_templates: dict[str, Any] | None = None

	def execute(self, ws: Any, effect_data: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
		effect_type = (effect_data or {}).get("effect")
		if not effect_type:
			return [{"type": "ExecutorError", "message": "missing effect type"}]

		match str(effect_type):
			case "ModifyProperty":
				return self._execute_modify_property(ws, effect_data, context)
			case "CreateEntity":
				return self._execute_create_entity(ws, effect_data, context)
			case "DestroyEntity":
				return self._execute_destroy_entity(ws, effect_data, context)
			case "TransferEntity":
				return self._execute_transfer_entity(ws, effect_data, context)
			case "AddCondition":
				return self._execute_add_condition(ws, effect_data, context)
			case "RemoveCondition":
				return self._execute_remove_condition(ws, effect_data, context)
			case "ConsumeInputs":
				return self._execute_consume_inputs(ws, effect_data, context)
			case "CreateTask":
				return self._execute_create_task(ws, effect_data, context)
			case "ProgressTask":
				return self._execute_progress_task(ws, effect_data, context)
			case "UpdateTaskStatus":
				return self._execute_update_task_status(ws, effect_data, context)
			case "FinishTask":
				return self._execute_finish_task(ws, effect_data, context)
			case _:
				return [{"type": "ExecutorError", "message": f"unknown effect type: {effect_type}"}]

	def _resolve_entity_from_ctx(self, ws: Any, ctx: dict[str, Any], key_or_idkey: str):
		id_key = key_or_idkey if str(key_or_idkey).endswith("_id") else f"{key_or_idkey}_id"
		eid = str((ctx or {}).get(id_key, ""))
		return ws.get_entity_by_id(eid)

	def _resolve_container_or_location_from_ctx(self, ws: Any, ctx: dict[str, Any], key_or_idkey: str):
		id_key = key_or_idkey if str(key_or_idkey).endswith("_id") else f"{key_or_idkey}_id"
		id_val = str((ctx or {}).get(id_key, ""))
		# Container: Entity with ContainerComponent; Location: location_id
		ent = ws.get_entity_by_id(id_val)
		if ent is not None and isinstance(ent.get_component("ContainerComponent"), ContainerComponent):
			return ent
		loc = ws.get_location_by_id(id_val)
		if loc is not None:
			return loc
		return None

	def _execute_modify_property(self, ws: Any, data: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
		target_key = data.get("target")
		target = self._resolve_entity_from_ctx(ws, context, str(target_key))
		if target is None:
			return [{"type": "ExecutorError", "message": "ModifyProperty: target missing"}]

		comp_name = str(data.get("component", ""))
		prop_name = str(data.get("property", ""))
		change = float(data.get("change", 0))

		comp = target.get_component(comp_name)
		if comp is None:
			return [{"type": "ExecutorError", "message": f"ModifyProperty: component missing: {comp_name}"}]

		# Currently only strictly supports numeric properties of CreatureComponent, other cases to be extended later
		if isinstance(comp, CreatureComponent):
			comp.ensure_initialized()
			cur = getattr(comp, prop_name, None)
			if cur is None:
				return [{"type": "ExecutorError", "message": f"ModifyProperty: property missing: {prop_name}"}]
			setattr(comp, prop_name, float(cur) + change)
			return [
				{
					"type": "PropertyModified",
					"entity_id": target.entity_id,
					"component": comp_name,
					"property": prop_name,
					"delta": change,
					"new_value": getattr(comp, prop_name),
				}
			]

		# UnknownComponent: Attempt to write to data dict
		if hasattr(comp, "data") and isinstance(getattr(comp, "data"), dict):
			cur = comp.data.get(prop_name, 0)
			try:
				comp.data[prop_name] = float(cur) + change
				return [
					{
						"type": "PropertyModified",
						"entity_id": target.entity_id,
						"component": comp_name,
						"property": prop_name,
						"delta": change,
						"new_value": comp.data[prop_name],
					}
				]
			except Exception:
				return [{"type": "ExecutorError", "message": "ModifyProperty: failed to write UnknownComponent"}]

		return [{"type": "ExecutorError", "message": "ModifyProperty: unsupported component type"}]

	def _execute_create_entity(self, ws: Any, data: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
		template_id = data.get("template")
		destination_data = data.get("destination")
		if not template_id or not isinstance(destination_data, dict):
			return [{"type": "ExecutorError", "message": "CreateEntity: missing template or destination"}]

		if not isinstance(self.entity_templates, dict):
			return [{"type": "ExecutorError", "message": "CreateEntity: executor has no entity_templates"}]

		template = self.entity_templates.get(str(template_id), {})
		if not isinstance(template, dict) or not template:
			return [{"type": "ExecutorError", "message": f"CreateEntity: template not found: {template_id}"}]

		# Assume existence: RuntimeEntityFactory
		# Intent: Separate runtime entity creation from load building; Necessity: CreateEntity needs to reuse template component building logic
		# Currently use minimal strategy: Reuse builder component building semantics (Only build migrated components, others as UnknownComponent)
		from ..data.builder import create_entity_from_template  # Local import to avoid circular dependency

		new_id = str(data.get("instance_id") or f"{template_id}_{uuid4().hex[:8]}")
		new_entity = create_entity_from_template(str(template_id), new_id, self.entity_templates)
		ws.register_entity(new_entity)

		dest_type = str(destination_data.get("type", ""))
		dest_target_key = destination_data.get("target")

		placed = False
		if dest_type == "container":
			parent = self._resolve_entity_from_ctx(ws, context, str(dest_target_key))
			if parent is not None:
				cc = parent.get_component("ContainerComponent")
				if isinstance(cc, ContainerComponent):
					if cc.add_entity(new_entity):
						# Double Indexing: Add to container's location index
						loc = ws.get_location_of_entity(parent.entity_id)
						if loc is not None:
							ws.ensure_entity_in_location(new_entity.entity_id, loc.location_id)
						placed = True

		elif dest_type == "location":
			agent = self._resolve_entity_from_ctx(ws, context, "agent")
			if agent is not None:
				loc = ws.get_location_of_entity(agent.entity_id)
				if loc is not None:
					ws.ensure_entity_in_location(new_entity.entity_id, loc.location_id)
					placed = True

		if not placed:
			# Fallback: Try to place at agent's location
			agent = self._resolve_entity_from_ctx(ws, context, "agent")
			loc = ws.get_location_of_entity(agent.entity_id) if agent is not None else None
			if loc is not None:
				ws.ensure_entity_in_location(new_entity.entity_id, loc.location_id)
				placed = True

		return [
			{
				"type": "EntityCreated",
				"entity_id": new_entity.entity_id,
				"template_id": new_entity.template_id,
				"placed": placed,
			}
		]

	def _execute_destroy_entity(self, ws: Any, data: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
		target_key = str(data.get("target", "entity_to_destroy"))
		ent = self._resolve_entity_from_ctx(ws, context, target_key)
		if ent is None:
			return [{"type": "ExecutorError", "message": "DestroyEntity: target missing"}]

		events: list[dict[str, Any]] = []

		# 0) If container, recursively destroy children first (Avoid dangling IDs)
		cc_self = ent.get_component("ContainerComponent")
		if isinstance(cc_self, ContainerComponent):
			for child_id in list(cc_self.get_all_item_ids()):
				events.extend(self.execute(ws, {"effect": "DestroyEntity", "target": "entity_to_destroy"}, {"entity_to_destroy_id": child_id}))

		# 1) Remove from all location indexes
		for loc in ws.locations.values():
			if ent.entity_id in loc.entities_in_location:
				loc.remove_entity_id(ent.entity_id)

		# 2) Remove from all containers (Iterate all ContainerComponent)
		for holder in ws.entities.values():
			cc = holder.get_component("ContainerComponent")
			if isinstance(cc, ContainerComponent):
				for slot in cc.slots.values():
					if ent.entity_id in slot.items:
						slot.items.remove(ent.entity_id)

		# 3) Delete entity itself
		ws.entities.pop(ent.entity_id, None)
		events.append({"type": "EntityDestroyed", "entity_id": ent.entity_id})
		return events

	def _execute_transfer_entity(self, ws: Any, data: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
		# Convention: context provides entity_id/source_id/destination_id (Consistent with your Godot version)
		entity_to_move = self._resolve_entity_from_ctx(ws, context, "entity_id")
		source_node = self._resolve_container_or_location_from_ctx(ws, context, "source_id")
		dest_node = self._resolve_container_or_location_from_ctx(ws, context, "destination_id")

		if entity_to_move is None or source_node is None or dest_node is None:
			return [{"type": "ExecutorError", "message": "TransferEntity: missing entity/source/destination"}]

		# Source/Target location (For cross-location cascading transfer)
		source_loc = None
		dest_loc = None
		if hasattr(source_node, "location_id"):
			source_loc = source_node
		else:
			source_loc = ws.get_location_of_entity(getattr(source_node, "entity_id", ""))
		if hasattr(dest_node, "location_id"):
			dest_loc = dest_node
		else:
			dest_loc = ws.get_location_of_entity(getattr(dest_node, "entity_id", ""))

		cross_location = False
		if source_loc is not None and dest_loc is not None:
			cross_location = str(source_loc.location_id) != str(dest_loc.location_id)

		# 1) Remove from source
		if hasattr(source_node, "location_id"):
			if cross_location:
				source_node.remove_entity_id(entity_to_move.entity_id)
		else:
			cc = source_node.get_component("ContainerComponent")
			if isinstance(cc, ContainerComponent):
				if not cc.remove_entity_by_id(entity_to_move.entity_id):
					return [{"type": "ExecutorError", "message": "TransferEntity: failed to remove from source container"}]

		# 2) Add to destination
		add_ok = False
		if hasattr(dest_node, "location_id"):
			add_ok = bool(dest_node.add_entity_id(entity_to_move.entity_id))
		else:
			cc = dest_node.get_component("ContainerComponent")
			if isinstance(cc, ContainerComponent):
				add_ok = bool(cc.add_entity(entity_to_move))
				if add_ok and dest_loc is not None:
					ws.ensure_entity_in_location(entity_to_move.entity_id, dest_loc.location_id)

		if not add_ok:
			return [{"type": "ExecutorError", "message": "TransferEntity: failed to add to destination"}]

		# 3) Cross-location cascading transfer (Container entity must bring descendants)
		if cross_location and source_loc is not None and dest_loc is not None:
			ids_to_move = [entity_to_move.entity_id]
			cc = entity_to_move.get_component("ContainerComponent")
			if isinstance(cc, ContainerComponent):
				ids_to_move.extend(ws.collect_descendant_item_ids(entity_to_move.entity_id))
			ws.move_ids_between_locations(ids_to_move, source_loc.location_id, dest_loc.location_id)

		return [{"type": "EntityTransferred", "entity_id": entity_to_move.entity_id}]

	def _get_or_create_conditions_list(self, entity: Any) -> list[str] | None:
		comp = entity.get_component("ConditionComponent")
		if comp is None:
			return None
		if hasattr(comp, "data") and isinstance(getattr(comp, "data"), dict):
			comp.data.setdefault("conditions", [])
			if isinstance(comp.data["conditions"], list):
				return comp.data["conditions"]
		return None

	def _execute_add_condition(self, ws: Any, data: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
		target_key = data.get("target")
		condition_id = data.get("condition_id")
		target = self._resolve_entity_from_ctx(ws, context, str(target_key))
		if target is None or not condition_id:
			return [{"type": "ExecutorError", "message": "AddCondition: missing target or condition_id"}]

		cond_list = self._get_or_create_conditions_list(target)
		if cond_list is None:
			return [{"type": "ExecutorError", "message": "AddCondition: ConditionComponent missing (not migrated yet)"}]

		cid = str(condition_id)
		if cid not in cond_list:
			cond_list.append(cid)
		return [{"type": "ConditionAdded", "entity_id": target.entity_id, "condition_id": cid}]

	def _execute_remove_condition(self, ws: Any, data: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
		target_key = data.get("target")
		condition_id = data.get("condition_id")
		target = self._resolve_entity_from_ctx(ws, context, str(target_key))
		if target is None or not condition_id:
			return [{"type": "ExecutorError", "message": "RemoveCondition: missing target or condition_id"}]

		cond_list = self._get_or_create_conditions_list(target)
		if cond_list is None:
			return [{"type": "ExecutorError", "message": "RemoveCondition: ConditionComponent missing (not migrated yet)"}]

		cid = str(condition_id)
		if cid in cond_list:
			cond_list.remove(cid)
		return [{"type": "ConditionRemoved", "entity_id": target.entity_id, "condition_id": cid}]

	def _execute_consume_inputs(self, ws: Any, data: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
		ids = (context or {}).get("entities_for_consumption_ids", []) or []
		events: list[dict[str, Any]] = []
		for eid in list(ids):
			events.extend(self.execute(ws, {"effect": "DestroyEntity", "target": "entity_to_destroy"}, {"entity_to_destroy_id": str(eid)}))
		return events

	def _execute_create_task(self, ws: Any, data: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
		target = self._resolve_entity_from_ctx(ws, context, "target")
		if target is None:
			return [{"type": "ExecutorError", "message": "CreateTask: target missing"}]
		recipe = (context or {}).get("recipe", {}) or {}
		if not isinstance(recipe, dict) or not recipe:
			return [{"type": "ExecutorError", "message": "CreateTask: recipe missing in context"}]

		agent_id = str((context or {}).get("agent_id", "") or "")
		agent = ws.get_entity_by_id(agent_id) if agent_id else None
		host_entity = agent if agent is not None else target

		host = host_entity.get_component("TaskHostComponent")
		if not isinstance(host, TaskHostComponent):
			# Compatible with old name: TaskComponent (Might still be called this during migration)
			host = host_entity.get_component("TaskComponent")
		if not isinstance(host, TaskHostComponent):
			host = TaskHostComponent()
			try:
				host_entity.add_component("TaskHostComponent", host)
			except Exception:
				return [{"type": "ExecutorError", "message": "CreateTask: failed to add TaskHostComponent"}]

		verb = str(recipe.get("verb", ""))
		task = Task(task_type=verb, target_entity_id=target.entity_id)
		task.action_type = "Task"
		process = recipe.get("process", {}) or {}
		task.required_progress = float(process.get("required_progress", 1))
		task.completion_effects = [x for x in (recipe.get("outputs", []) or []) if isinstance(x, dict)]

		# Progressor config: Prioritize recipe["progression"], then process["progression"]
		prog = recipe.get("progression", None)
		if prog is None:
			prog = process.get("progression", {}) or {}
		if isinstance(prog, dict):
			task.progressor_id = str(prog.get("progressor", prog.get("progressor_id", "")) or "")
			params = prog.get("params", {}) or {}
			if isinstance(params, dict):
				task.progressor_params = dict(params)
			task.tick_effects = [x for x in (prog.get("tick_effects", []) or []) if isinstance(x, dict)]

		host.add_task(task)
		ws.register_task(task)
		context["created_task_id"] = task.task_id

		events: list[dict[str, Any]] = [{"type": "TaskCreated", "task_id": task.task_id, "target_entity_id": target.entity_id}]

		# If context provides agent_id, assign task to that agent by default, and occupy action rights (WorkerComponent.current_task_id)
		if agent is not None:
			worker = agent.get_component("WorkerComponent")
			if isinstance(worker, WorkerComponent):
				try:
					worker.assign_task(task.task_id)
				except Exception:
					pass
				task.task_status = "InProgress"
				if agent_id and agent_id not in task.assigned_agent_ids:
					task.assigned_agent_ids.append(agent_id)
				events.append({"type": "TaskAssigned", "task_id": task.task_id, "agent_id": agent_id})

		return events

	def _execute_progress_task(self, ws: Any, data: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
		task_id = str(data.get("task_id") or (context or {}).get("task_id", "") or "")
		delta = float(data.get("delta", 0.0))
		task = ws.get_task_by_id(task_id) if hasattr(ws, "get_task_by_id") else None

		if task is None:
			return [{"type": "ExecutorError", "message": f"ProgressTask: task not found {task_id}"}]

		task.progress += delta
		return [
			{
				"type": "TaskProgressed",
				"task_id": task.task_id,
				"delta": delta,
				"new_progress": task.progress,
				"required": task.required_progress,
			}
		]

	def _execute_update_task_status(self, ws: Any, data: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
		task_id = str(data.get("task_id") or (context or {}).get("task_id", "") or "")
		new_status = str(data.get("status", "")).strip()
		task = ws.get_task_by_id(task_id) if hasattr(ws, "get_task_by_id") else None

		if task is None:
			return [{"type": "ExecutorError", "message": f"UpdateTaskStatus: task not found {task_id}"}]

		old_status = getattr(task, "task_status", "Unknown")
		task.task_status = new_status
		return [
			{
				"type": "TaskStatusChanged",
				"task_id": task.task_id,
				"old_status": old_status,
				"new_status": new_status,
			}
		]

	def _execute_finish_task(self, ws: Any, data: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
		task_id = str((context or {}).get("task_id", ""))
		task = ws.get_task_by_id(task_id)
		if task is None:
			return [{"type": "ExecutorError", "message": "FinishTask: task not found"}]

		# Critical Fix: Completion effects usually take recipe's target/agent as semantic target,
		# But FinishTask context might only contain task_id/agent_id.
		# To make completion_effects like {"target": "target"} executable,
		# Fill target_id here (Defaults to task target entity).
		if isinstance(context, dict):
			context.setdefault("target_id", str(getattr(task, "target_entity_id", "") or ""))

		# Execute completion effects (Prioritize completion_effects solidified in task)
		effects = list(task.completion_effects or [])
		if not effects:
			recipe = (context or {}).get("recipe", {}) or {}
			if isinstance(recipe, dict):
				effects = [x for x in (recipe.get("outputs", []) or []) if isinstance(x, dict)]

		events: list[dict[str, Any]] = []
		for eff in effects:
			events.extend(self.execute(ws, eff, context))

		# Remove from host + Global unregister
		host_entity = None
		agent_id = str((context or {}).get("agent_id", "") or "")
		if agent_id:
			host_entity = ws.get_entity_by_id(agent_id)
		if host_entity is None:
			host_entity = ws.get_entity_by_id(task.target_entity_id)

		if host_entity is not None:
			host = host_entity.get_component("TaskHostComponent")
			if not isinstance(host, TaskHostComponent):
				host = host_entity.get_component("TaskComponent")
			if isinstance(host, TaskHostComponent):
				host.remove_task(task.task_id)

		ws.unregister_task(task.task_id)
		events.append({"type": "TaskFinished", "task_id": task.task_id})
		return events
