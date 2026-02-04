from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..models.world_state import WorldState


@dataclass
class WorldManager:
	"""
	Python version of WorldManager (Automatic simulation loop scheduler).

	Responsibilities:
	- Advance time (tick)
	- Call per_tick of all components every tick
	- Let DecisionArbiter decide if decision-making is needed
	- Pass agent actions to interaction engine, then to executor to modify the world

	Explanation:
	- This class does not directly write WorldState details; specific writes should be done by executor.
	"""

	world_state: WorldState
	interaction_engine: Any
	executor: Any
	perception_system: Any
	action_provider: Any

	is_running: bool = False
	ticks_per_step: int = 1

	# Optional: Route different action providers by provider_id (Player/LLM/Script/Replay, etc.)
	# If an entity's controller provider_id is not in this table, the entity will not produce actions in the decision loop (Safe default).
	action_providers: dict[str, Any] = field(default_factory=dict)

	def run(self, max_ticks: int = 1) -> list[dict[str, Any]]:
		"""
		Run for max_ticks ticks, return accumulated event list.
		"""
		self.is_running = True
		all_events: list[dict[str, Any]] = []
		for _ in range(int(max_ticks)):
			if not self.is_running:
				break
			all_events.extend(self.step())
		return all_events

	def stop(self) -> None:
		self.is_running = False

	def step(self) -> list[dict[str, Any]]:
		"""
		Advance one simulation tick (Turn-based).
		"""
		events: list[dict[str, Any]] = []

		# 1) Advance time
		self.world_state.game_time.advance_ticks(self.ticks_per_step)
		events.append(
			{
				"type": "TickAdvanced",
				"total_ticks": self.world_state.game_time.total_ticks,
				"time": self.world_state.game_time.time_to_string(),
			}
		)
		verbose = str(__import__("os").environ.get("VERBOSE_EVENTS", "") or "").strip() == "1"
		if verbose:
			print(f"\n[Tick] {events[-1]['total_ticks']} time={events[-1]['time']}")
		# Tick events are also written to world log (for debug/replay; LLM usually doesn't need such detail, can filter in observation layer).
		self.world_state.record_event(events[-1], {"actor_id": ""})

		# 2) Inject runtime services: Allow components to complete "Decision -> Translation -> Output effects" in per_tick phase.
		# Convention: Components do not directly write to world, only write to ws.pending_effects; Manager handles unified execution (Single write entry).
		self.world_state.services = {
			"perception_system": self.perception_system,
			"interaction_engine": self.interaction_engine,
			"default_action_provider": self.action_provider,
			"action_providers": dict(self.action_providers or {}),
			# Allow controllers to "take effect immediately" within the same tick:
			# Intent: Support Grounder multi-step action sequences, and immediate world state change after failure;
			# Necessity: Otherwise controllers repeatedly see old world in per_tick, executing the same action repeatedly.
			# "flush_effects": lambda: self._flush_pending_effects(events),
            # NEW: Direct executor access
            "executor": self.executor,
            "events_accumulator": events, # Pass the accumulator to allow executor wrapper to append events
		}

        # Helper wrapper to be used by components via ws.services["execute"](...)
		def execute_wrapper(effect: dict[str, Any], context: dict[str, Any]) -> None:
			verbose = str(__import__("os").environ.get("VERBOSE_EVENTS", "") or "").strip() == "1"
			if verbose:
				print(f"[Effect] {effect.get('effect')}: {effect} ctx={context}")
			result_events = self.executor.execute(self.world_state, effect, context)
			for ev in list(result_events or []):
				if isinstance(ev, dict):
					self.world_state.record_event(ev, context)
					if verbose:
						print(f"[Event] {ev.get('type')}: {ev}")
			events.extend(result_events)

		self.world_state.services["execute"] = execute_wrapper

		# 3) per_tick: Process in entity order (The "Time-stop + Single-thread" semantics you want)
		# And execute pending_effects immediately after each entity processing (Make world state effective immediately).
		for ent_id, ent in list(self.world_state.entities.items()):
			for _comp_name, comp in list(ent.components.items()):
				per_tick = getattr(comp, "per_tick", None)
				if callable(per_tick):
					per_tick(self.world_state, ent_id, self.ticks_per_step)

			# 3.5) 
			# REMOVED: Immediate execution mode, no more flush_pending_effects
			# self._flush_pending_effects(events)

		return events

	# def _flush_pending_effects(self, events: list[dict[str, Any]]) -> None:
	# 	"""
	# 	Execute pending_effects uniformly (Single write entry).
	#
	# 	Explanation:
	# 	- Allows multiple calls within a tick (e.g., AgentControl actively flushes after multi-step action)
	# 	- events is "accumulated event list for current tick", will be appended in place
	# 	"""
	# 	verbose = str(__import__("os").environ.get("VERBOSE_EVENTS", "") or "").strip() == "1"
	#
	# 	while self.world_state.pending_effects:
	# 		item = self.world_state.pending_effects.pop(0)
	# 		eff = item.get("effect", {})
	# 		ctx = item.get("context", {})
	# 		if isinstance(eff, dict) and isinstance(ctx, dict):
	# 			if verbose:
	# 				print(f"[Effect] {eff.get('effect')}: {eff} ctx={ctx}")
	# 			result_events = self.executor.execute(self.world_state, eff, ctx)
	# 			for ev in list(result_events or []):
	# 				if isinstance(ev, dict):
	# 					self.world_state.record_event(ev, ctx)
	# 					if verbose:
	# 						print(f"[Event] {ev.get('type')}: {ev}")
	# 			events.extend(result_events)

