from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class AgentControlComponent:
	"""
	Agent control switch ("Is this entity allowed to be driven by Agent/LLM").

	Design Goals:
	- Explicit Authorization: Only entities with this component attached will enter the decision loop.
	- Extensible: Different control modes/providers (LLM/Script/Replay) can be attached here in the future.
	"""

	# Whether control is enabled (can be used to temporarily "freeze" an agent)
	enabled: bool = True

	# Control provider identifier (e.g., llm/openai, policy/simple, replay/xxx)
	# Currently not used in the main loop, but the field is reserved for future extension and display.
	provider_id: str = ""

	def per_tick(self, _ws: Any, _entity_id: str, _ticks_per_minute: int) -> None:
		"""
		Synchronous Decision (Time-stop semantics):
		- Complete "Arbitration -> Perception -> Action Generation -> Recipe Translation -> Output effects" within the same tick.
		- Effects are not executed directly here (left to Manager/Executor), only written to ws.pending_effects.
		"""

		ws = _ws
		agent_id = str(_entity_id)

		if not bool(self.enabled):
			return

		agent = ws.get_entity_by_id(agent_id) if hasattr(ws, "get_entity_by_id") else None
		if agent is None:
			return

		arb = agent.get_component("DecisionArbiterComponent")
		if arb is None or not hasattr(arb, "check_if_interrupt_is_needed"):
			return

		interrupt = arb.check_if_interrupt_is_needed(ws, agent_id)
		if not getattr(interrupt, "interrupt", False):
			return

		# Important: Even if there is a current task, the interrupt module (e.g., LowNutrition) must be queried.
		# If interruption is needed, pause the current task and clear current_task_id (let "urgent needs" preempt action rights).
		worker = agent.get_component("WorkerComponent")
		current_task_id = str(getattr(worker, "current_task_id", "") or "") if worker is not None else ""
		if worker is not None and current_task_id:
			task = ws.get_task_by_id(current_task_id) if hasattr(ws, "get_task_by_id") else None
			if task is not None and hasattr(task, "task_status"):
				# Use Effect to modify task status
				execute = ws.services.get("execute")
				if callable(execute):
					execute(
						{
							"effect": "UpdateTaskStatus",
							"task_id": current_task_id,
							"status": "Paused",
						},
						{"agent_id": agent_id, "task_id": current_task_id},
					)
			try:
				worker.stop_task()
			except Exception:
				pass
			# Record a "TaskInterrupted" event (can be used for observation/debugging)
			if hasattr(ws, "record_event"):
				try:
					ws.record_event(
						{"type": "TaskInterrupted", "task_id": current_task_id, "reason": str(getattr(interrupt, "reason", "") or "")},
						{"actor_id": agent_id},
					)
				except Exception:
					pass

		services = getattr(ws, "services", {}) or {}
		perception_system = services.get("perception_system")
		interaction_engine = services.get("interaction_engine")
		default_action_provider = services.get("default_action_provider")
		action_providers = services.get("action_providers", {}) or {}

		# Select action provider: Prioritize this component's provider_id; fallback to default if empty.
		pid = str(self.provider_id or "").strip()
		action_provider = default_action_provider if not pid else action_providers.get(pid)
		if action_provider is None:
			return

		if perception_system is None or not hasattr(perception_system, "perceive"):
			return
		if interaction_engine is None or not hasattr(interaction_engine, "process_command"):
			return

		# Fuse: Prevent infinite instantaneous action output within a tick from freezing the system.
		max_actions_in_tick = 50
		actions_executed = 0

		# Critical: reason/interrupt must "refresh as the world changes"
		# - If not refreshed, issues like: ate an apple (nutrition restored) but next round still uses old reason="LowNutrition" will occur.
		# - Whether decision rights exist (interrupt=True/False) should also change with the world.
		reason = str(getattr(interrupt, "reason", "") or "")

		while True:
			# If current_task has already been obtained in other systems, stop further decision making.
			worker = agent.get_component("WorkerComponent")
			if worker is not None and bool(getattr(worker, "current_task_id", "")):
				break

			# Re-arbitrate every round: Get the latest interrupt + reason
			interrupt = arb.check_if_interrupt_is_needed(ws, agent_id)
			if not getattr(interrupt, "interrupt", False):
				break
			reason = str(getattr(interrupt, "reason", "") or "")

			# LLM needs "detailed event stream", so include interactions in perception (perception system handles filtering internally).
			# Note: effects/event_log are granular, Planner mainly consumes interactions (recipe/attempt level narrative).
			perception = perception_system.perceive(ws, agent_id, include_interactions=True)
			# Inject recipe_db additionally for LLM side (used to generate available verb set; avoid n*m action list).
			services = getattr(ws, "services", {}) or {}
			engine = services.get("interaction_engine")
			if engine is not None and hasattr(engine, "recipe_db") and isinstance(getattr(engine, "recipe_db"), dict):
				perception["recipe_db"] = dict(getattr(engine, "recipe_db"))

			# Compatible with different decide signatures: decide(perception, reason) or decide(perception, reason, agent_id)
			try:
				actions = action_provider.decide(perception, reason, agent_id)
			except TypeError:
				actions = action_provider.decide(perception, reason)

			if not actions:
				break

			for action in actions:
				result = interaction_engine.process_command(ws, agent_id, action)
				status = str((result or {}).get("status", "") or "")
				verb = str((action or {}).get("verb", "") or "")
				target_id = str((action or {}).get("target_id", "") or "")

				if status != "success":
					# Record failed action attempts (for Planner; failure usually means "World/Memory mismatch").
					reason_code = str((result or {}).get("reason", "") or "")
					if hasattr(ws, "record_interaction_attempt"):
						ws.record_interaction_attempt(
							actor_id=agent_id,
							verb=verb,
							target_id=target_id,
							status="failed",
							reason=reason_code,
							recipe_id="",
						)
					# Do not raise exception, avoid one agent crashing the entire simulation; stop subsequent actions.
					return

				ctx = (result or {}).get("context", {}) or {}
				# Record successful action attempts (recipe_id used for subsequent template rendering)
				recipe_id = ""
				try:
					recipe_id = str(((ctx or {}).get("recipe", {}) or {}).get("id", "") or "")
				except Exception:
					recipe_id = ""
				if hasattr(ws, "record_interaction_attempt"):
					ws.record_interaction_attempt(
						actor_id=agent_id,
						verb=verb,
						target_id=target_id,
						status="success",
						reason="",
						recipe_id=recipe_id,
					)

				for eff in (result or {}).get("effects", []) or []:
					if isinstance(eff, dict) and isinstance(ctx, dict):
						execute = ws.services.get("execute")
						if callable(execute):
							execute(eff, ctx)

				# Critical: Execute effects immediately, updating world state within the same tick.
				# Intent: Support Grounder multi-step action sequences; also avoid repeatedly seeing the same target leading to infinite Consume.
				# REMOVED: Immediate execution mode is now default via 'execute' wrapper
				# services = getattr(ws, "services", {}) or {}
				# flush = services.get("flush_effects")
				# if callable(flush):
				# 	try:
				# 		flush()
				# 	except Exception:
				# 		# flush failure should not crash the entire simulation
				# 		return

				worker_after = agent.get_component("WorkerComponent")
				if worker_after is not None and bool(getattr(worker_after, "current_task_id", "")):
					return

				actions_executed += 1
				if actions_executed >= max_actions_in_tick:
					return

			# Next loop start will re-arbitrate & check current_task
