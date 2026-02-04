from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4
from typing import Any


@dataclass
class Task:
	"""
	对齐 Godot `Task.gd` 的数据结构（Resource -> Python dataclass）。
	"""

	task_id: str = field(default_factory=lambda: f"task_{uuid4().hex}")
	task_type: str = ""
	action_type: str = "Action"  # Action, Task
	target_entity_id: str = ""

	progress: float = 0.0
	required_progress: float = 100.0

	multiple_entity: bool = False
	assigned_agent_ids: list[str] = field(default_factory=list)

	task_status: str = "Inactive"  # Inactive/InProgress/Paused/Completed
	parameters: dict[str, Any] = field(default_factory=dict)

	# --- 推进器（Progressor）配置：由 recipe 固化进 task ---
	progressor_id: str = ""
	progressor_params: dict[str, Any] = field(default_factory=dict)
	tick_effects: list[dict[str, Any]] = field(default_factory=list)

	# 任务完成时要执行的效果列表（由创建时写入，完成时读取）
	completion_effects: list[dict[str, Any]] = field(default_factory=list)

	def is_complete(self) -> bool:
		return float(self.progress) >= float(self.required_progress)

	def get_remaining_progress(self) -> float:
		return max(0.0, float(self.required_progress) - float(self.progress))

