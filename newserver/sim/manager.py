from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..models.world_state import WorldState


@dataclass
class WorldManager:
	"""
	Python 版 WorldManager（自动模拟闭环的调度器）。

	职责：
	- 推进时间（tick）
	- 每 tick 调用所有组件的 per_tick
	- 让 DecisionArbiter 决定是否需要进入决策
	- 将 agent 的动作交给交互引擎，再交给执行器改写世界

	说明：
	- 本类不直接写 WorldState 的细节；具体写入应由 executor 完成。
	"""

	world_state: WorldState
	interaction_engine: Any
	executor: Any
	perception_system: Any
	action_provider: Any

	is_running: bool = False
	ticks_per_step: int = 1

	# 可选：按 provider_id 路由不同的动作提供者（玩家/LLM/脚本/回放等）
	# 若某实体的控制器 provider_id 不在此表中，则该实体不会在决策循环中产出动作（安全默认）。
	action_providers: dict[str, Any] = field(default_factory=dict)

	def run(self, max_ticks: int = 1) -> list[dict[str, Any]]:
		"""
		运行 max_ticks 次 tick，返回累计事件列表。
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
		推进一个模拟 tick（回合制）。
		"""
		events: list[dict[str, Any]] = []

		# 1) 推进时间
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
		# Tick 事件也写入世界日志（便于调试/回放；LLM 通常不需要看到这么细，可在观测层过滤）
		self.world_state.record_event(events[-1], {"actor_id": ""})

		# 2) 注入运行期服务：让组件在 per_tick 阶段就能完成“决策→翻译→产出 effects”
		# 约定：组件不直接写世界，只写入 ws.pending_effects；Manager 负责统一执行（写入口唯一）。
		self.world_state.services = {
			"perception_system": self.perception_system,
			"interaction_engine": self.interaction_engine,
			"default_action_provider": self.action_provider,
			"action_providers": dict(self.action_providers or {}),
			# 允许控制器在同一 tick 内“立刻生效”：
			# 用意：支持 Grounder 多步 action 串，以及失败后世界状态即时变化；
			# 必要性：否则控制器在 per_tick 内反复看到旧世界，会重复执行同一动作。
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

		# 3) per_tick：按实体顺序处理（你想要的“时间停止 + 单线程”语义）
		# 并在每个实体处理结束后立刻执行 pending_effects（让世界状态即时生效）。
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
	# 	统一执行 pending_effects（写入口唯一）。
	# 
	# 	说明：
	# 	- 允许在 tick 内被多次调用（例如 AgentControl 多步 action 后主动 flush）
	# 	- events 为“当前 tick 的累计事件列表”，会就地 append
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

