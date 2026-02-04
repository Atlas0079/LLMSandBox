from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..models.components import ContainerComponent


@dataclass
class PerceptionSystem:
	"""
	Perception System:
	- Provide agent's location
	- Provide "visible" entities in location (including id/name/tags)

	V2 (Current Implementation): Support container visibility
	- Location stores "Spatial Index": entities_in_location
	- Container (ContainerComponent) stores "Containment Relationship"
	- Default: Contained entities are invisible
	- If container slot.config.transparent == true: Entities in that slot are visible, and transparent contents are recursively expanded.
	"""

	def get_visible_events(self, ws: Any, agent_id: str, max_events: int = 20, tick_window: int = 10) -> list[dict[str, Any]]:
		"""
		Observe "Dynamic Information": Return recent visible events for agent.

		Rules (MVP):
		- Only return events happening in the same location as agent (same location_id)
		- Only return events within the last tick_window ticks
		- Return last max_events items
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
			# Expose only minimal necessary fields to upper layer (avoid coupling internal structure)
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
		Observe "Readable Interaction Log": Return recent visible action attempts for viewer (recipe/interaction level).

		Rules (MVP):
		- Only return records happening in the same location as viewer (same location_id)
		- Only return records within the last tick_window ticks
		- Return last max_records items

		Return Structure:
		- [{"tick": int, "text": str, "actor_id": str, "status": str, "reason": str}]
		"""

		loc = ws.get_location_of_entity(viewer_id)
		if loc is None:
			return []

		loc_id = str(getattr(loc, "location_id", "") or "")
		now_tick = int(getattr(getattr(ws, "game_time", None), "total_ticks", 0))
		min_tick = max(0, now_tick - int(tick_window))

		# Get recipe_db from runtime services (for template rendering)
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
		Render an interaction_log record into natural language narrative (MVP: Only supports "Me/Other Name" perspectives).
		"""

		actor_id = str(record.get("actor_id", "") or "")
		actor_name = str(record.get("actor_name", "") or actor_id)
		target_name = str(record.get("target_name", "") or str(record.get("target_id", "") or ""))
		verb = str(record.get("verb", "") or "")
		status = str(record.get("status", "") or "")
		reason = str(record.get("reason", "") or "")
		recipe_id = str(record.get("recipe_id", "") or "")

		actor_text = "Me" if str(viewer_id) == actor_id else actor_name

		reason_map = {
			"NO_TARGET": "Target not found",
			"NO_RECIPE": "No corresponding interaction rule",
			"TASK_NOT_IMPLEMENTED": "Continuous task not yet implemented",
		}
		reason_text = reason_map.get(reason, reason or "Unknown reason")

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
				template = "{actor} executed {verb} ({target})"
			else:
				template = "{actor} attempted {verb} ({target}) but failed: {reason}"

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

		# 1) Calculate "Contained Entity ID Set" (Used to filter hidden entities from location list)
		contained_ids = self._collect_contained_ids_in_location(ws, loc.location_id)

		# 2) Top-level visible entities: In location but not in any container
		visible_ids: list[str] = []
		for eid in list(loc.entities_in_location):
			if eid not in contained_ids:
				visible_ids.append(str(eid))

		# 3) Recursively expand visible contents of transparent containers
		visible_ids = self._expand_transparent_contents(ws, visible_ids)

		# 4) Output visible entity information
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
			# For debugging (Frontend can hide)
			"hidden_entity_count": max(0, len(loc.entities_in_location) - len(visible_ids)),
		}
		if include_events:
			result["events"] = self.get_visible_events(ws, agent_id)
		if include_interactions:
			result["interactions"] = self.get_visible_interactions(ws, agent_id)
		return result

	def _collect_contained_ids_in_location(self, ws: Any, location_id: str) -> set[str]:
		"""
		Collect entity ID set "belonging to a container" (Limited to same location).
		Intent: Filter contained entities from location list (Opaque by default invisible).
		Necessity: Location index and container relationship are orthogonal, skipping this leads to agent omniscience.
		"""
		contained: set[str] = set()

		for ent in list(getattr(ws, "entities", {}).values()):
			if ent is None:
				continue
			# Only count containers in the same location, avoid cross-location container leakage
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
		Recursively expand contents of all transparent slots starting from "Top-level visible entities".
		Rule: Only when a container entity itself is visible, can entities within its transparent slots be seen.
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

			# Only expand transparent slots
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
