from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...progressors import get_progressor


@dataclass
class WorkerComponent:
	"""
	Align with core field shape of Godot `WorkerComponent.gd`:
	- current_task_id: Currently progressing task (occupies action rights)

	Note:
	- Task progression implemented: Computes progress delta via progressor every tick and accumulates to task.progress.
	- Also writes task.tick_effects and FinishTask upon completion to ws.pending_effects,
	  Unified execution by WorldManager via WorldExecutor (maintaining "single write entry").
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
			# Task lost: Clear, leave to IdleRule to trigger decision
			self.stop_task()
			return

		# 1) Advance progress
		pid = str(getattr(task, "progressor_id", "") or "Linear")
		progressor = get_progressor(pid)
		delta = float(progressor.compute_progress_delta(ws, agent_id, task, ticks))
		
		# Use Effect to advance progress
		execute = ws.services.get("execute")
		if callable(execute):
			execute(
				{
					"effect": "ProgressTask",
					"task_id": task.task_id,
					"delta": delta,
				},
				{"agent_id": agent_id, "task_id": task.task_id},
			)

		if verbose:
			print(
				f"[Task] tick={getattr(ws.game_time, 'total_ticks', '')} agent={agent_id} "
				f"task={task.task_id} type={task.task_type} progress={task.progress:.2f}/{task.required_progress:.2f} (+{delta:.2f})"
			)

		# 2) Execute per-tick effects (consumed by Manager)
		for eff in list(getattr(task, "tick_effects", []) or []):
			if isinstance(eff, dict):
				if callable(execute):
					execute(
						eff,
						{"agent_id": agent_id, "task_id": task.task_id, "target_id": task.target_entity_id},
					)

		# 3) Completion check: Queue FinishTask if complete, and clear current_task (allow decision rights to emerge)
		if task.is_complete():
			if callable(execute):
				execute(
					{"effect": "FinishTask"},
					{"agent_id": agent_id, "task_id": task.task_id, "target_id": task.target_entity_id},
				)
			self.stop_task()

