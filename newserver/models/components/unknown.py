from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class UnknownComponent:
	"""
	用于承接未迁移组件（例如 Equipment/Condition/Perception/LLMControl 等）的原始字典。
	"""

	data: dict[str, Any] = field(default_factory=dict)

	def per_tick(self, _ws: Any, _entity_id: str, _ticks_per_minute: int) -> None:
		# 未迁移组件默认不推进任何状态
		return

