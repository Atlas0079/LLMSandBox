from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...progressors import get_progressor


@dataclass
class WorkerComponent:
	"""
	对齐 Godot `WorkerComponent.gd` 的核心字段形状：
	- current_task_id：当前正在推进的任务（占用行动权）

	注意：
	- 任务推进已实现：每 tick 会通过 progressor 计算进度增量并累加到 task.progress。
	- 同时会把 task.tick_effects 与完成时的 FinishTask 写入 ws.pending_effects，
	  由 WorldManager 统一交给 WorldExecutor 执行（保持“写入口唯一”）。
	"""

	current_task_id: str = ""

	def has_task(self) -> bool:
		return bool(self.current_task_id)

	def assign_task(self, task_id: str) -> None:
		self.current_task_id = str(task_id or "")

	def stop_task(self) -> None:
		self.current_task_id = ""

	def per_tick(self, _ws: Any, _entity_id: str, _ticks_per_minute: int) -> None:
		ws = _ws
		agent_id = str(_entity_id)
		ticks = int(_ticks_per_minute)
		verbose = str(__import__("os").environ.get("VERBOSE_EVENTS", "") or "").strip() == "1"

		if not self.has_task():
			return

		task = ws.get_task_by_id(self.current_task_id) if hasattr(ws, "get_task_by_id") else None
		if task is None:
			# 任务丢失：清空，交给 IdleRule 触发决策
			self.stop_task()
			return

		# 1) 推进进度
		pid = str(getattr(task, "progressor_id", "") or "Linear")
		progressor = get_progressor(pid)
		delta = float(progressor.compute_progress_delta(ws, agent_id, task, ticks))
		
		# 使用 Effect 推进进度
		ws.pending_effects.append(
			{
				"effect": {
					"effect": "ProgressTask",
					"task_id": task.task_id,
					"delta": delta,
				},
				"context": {"agent_id": agent_id, "task_id": task.task_id},
			}
		)

		if verbose:
			print(
				f"[Task] tick={getattr(ws.game_time, 'total_ticks', '')} agent={agent_id} "
				f"task={task.task_id} type={task.task_type} progress={task.progress:.2f}/{task.required_progress:.2f} (+{delta:.2f})"
			)

		# 2) 执行每 tick 效果（交给 Manager 消费）
		for eff in list(getattr(task, "tick_effects", []) or []):
			if isinstance(eff, dict):
				ws.pending_effects.append(
					{
						"effect": eff,
						"context": {"agent_id": agent_id, "task_id": task.task_id, "target_id": task.target_entity_id},
					}
				)

		# 3) 完成判定：完成则排队 FinishTask，并清空 current_task（让决策权出现）
		if task.is_complete():
			ws.pending_effects.append(
				{
					"effect": {"effect": "FinishTask"},
					"context": {"agent_id": agent_id, "task_id": task.task_id, "target_id": task.target_entity_id},
				}
			)
			self.stop_task()

