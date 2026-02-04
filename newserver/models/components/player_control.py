from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PlayerControlComponent:
	"""
	玩家控制器（占位实现）。

	用意：
	- 让“谁来控制这个实体”成为可插拔组件，而不是 Manager 里写死。
	- 未来你可以把来自前端（Godot/输入设备）的指令写入某个队列，再由本组件把队列转为 action。
	"""

	enabled: bool = True
	provider_id: str = "player"

