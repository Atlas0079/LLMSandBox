from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class AgentControlComponent:
	"""
	Agent 控制开关（“这个实体是否允许被 Agent/LLM 驱动”）。

	设计目标：
	- 显式授权：只有挂载了该组件的实体，才会进入决策循环。
	- 可扩展：未来可以在这里挂不同的控制模式/提供者（LLM/脚本/回放）。
	"""

	# 是否启用控制（可用于临时“冻结”某个 agent）
	enabled: bool = True

	# 控制提供者标识（例如：llm/openai、policy/simple、replay/xxx）
	# 目前主循环还没用到，但保留字段方便后续扩展与展示。
	provider_id: str = ""

	def per_tick(self, _ws: Any, _entity_id: str, _ticks_per_minute: int) -> None:
		"""
		同步决策（时间停止语义）：
		- 在同一个 tick 内完成“仲裁→感知→动作生成→配方翻译→产出 effects”。
		- 不在这里直接执行 effect（仍交给 Manager/Executor），只写入 ws.pending_effects。
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

		# 重要：即使当前有 task，也必须询问中断模块（例如 LowNutrition）
		# 若需要中断，则暂停当前任务并清空 current_task_id（让“紧急需求”抢占行动权）。
		worker = agent.get_component("WorkerComponent")
		current_task_id = str(getattr(worker, "current_task_id", "") or "") if worker is not None else ""
		if worker is not None and current_task_id:
			task = ws.get_task_by_id(current_task_id) if hasattr(ws, "get_task_by_id") else None
			if task is not None and hasattr(task, "task_status"):
				# 使用 Effect 修改任务状态
				ws.pending_effects.append(
					{
						"effect": {
							"effect": "UpdateTaskStatus",
							"task_id": current_task_id,
							"status": "Paused",
						},
						"context": {"agent_id": agent_id, "task_id": current_task_id},
					}
				)
			try:
				worker.stop_task()
			except Exception:
				pass
			# 记录一个“任务被中断”的事件（可用于观测/调试）
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

		# 选择动作提供者：优先用本组件 provider_id；为空则回退默认
		pid = str(self.provider_id or "").strip()
		action_provider = default_action_provider if not pid else action_providers.get(pid)
		if action_provider is None:
			return

		if perception_system is None or not hasattr(perception_system, "perceive"):
			return
		if interaction_engine is None or not hasattr(interaction_engine, "process_command"):
			return

		# 保险丝：避免一个 tick 内无限产出瞬时动作卡死
		max_actions_in_tick = 50
		actions_executed = 0

		# 关键：reason/interrupt 必须“随世界变化而刷新”
		# - 如果不刷新，会出现：吃完苹果（营养已恢复）但下一轮仍沿用旧 reason="营养过低"
		# - 决策权是否存在（interrupt=True/False）也应随世界变化而变
		reason = str(getattr(interrupt, "reason", "") or "")

		while True:
			# 若已经在其他系统里拿到了 current_task，则停止继续决策
			worker = agent.get_component("WorkerComponent")
			if worker is not None and bool(getattr(worker, "current_task_id", "")):
				break

			# 每轮都重新仲裁：获取最新的 interrupt + reason
			interrupt = arb.check_if_interrupt_is_needed(ws, agent_id)
			if not getattr(interrupt, "interrupt", False):
				break
			reason = str(getattr(interrupt, "reason", "") or "")

			# LLM 需要“详细事件流”，所以在感知里附带 interactions（感知系统内部负责过滤）
			# 注意：effects/event_log 很碎，Planner 主要吃 interactions（recipe/attempt 级叙事）
			perception = perception_system.perceive(ws, agent_id, include_interactions=True)
			# 给 LLM 侧额外注入 recipe_db（用于生成可用 verb 集合；避免 n×m 的动作列表）
			services = getattr(ws, "services", {}) or {}
			engine = services.get("interaction_engine")
			if engine is not None and hasattr(engine, "recipe_db") and isinstance(getattr(engine, "recipe_db"), dict):
				perception["recipe_db"] = dict(getattr(engine, "recipe_db"))

			# 兼容不同 decide 签名：decide(perception, reason) 或 decide(perception, reason, agent_id)
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
					# 记录失败的动作尝试（给 Planner 用；失败通常意味着“世界/记忆不符”）
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
					# 不抛异常，避免一个 agent 让整个模拟崩溃；停止后续动作
					return

				ctx = (result or {}).get("context", {}) or {}
				# 记录成功的动作尝试（recipe_id 用于后续渲染模板）
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
						ws.pending_effects.append({"effect": eff, "context": ctx})

				# 关键：立刻执行 effects，让世界状态在同一 tick 内更新
				# 用意：支持 Grounder 多步 action 串；也避免重复看到同一个目标导致无限 Consume。
				services = getattr(ws, "services", {}) or {}
				flush = services.get("flush_effects")
				if callable(flush):
					try:
						flush()
					except Exception:
						# flush 失败不应让整个模拟崩溃
						return

				worker_after = agent.get_component("WorkerComponent")
				if worker_after is not None and bool(getattr(worker_after, "current_task_id", "")):
					return

				actions_executed += 1
				if actions_executed >= max_actions_in_tick:
					return

			# 下一轮循环开始会重新仲裁 & 检查 current_task
