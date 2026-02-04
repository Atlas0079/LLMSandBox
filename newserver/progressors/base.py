from __future__ import annotations

from typing import Protocol, Any


class Progressor(Protocol):
	"""
	任务推进器接口：
	- 输入：world_state、agent_id、task、ticks
	- 输出：本 tick 增加的 progress（float）
	"""

	progressor_id: str

	def compute_progress_delta(self, ws: Any, agent_id: str, task: Any, ticks: int) -> float:
		...

