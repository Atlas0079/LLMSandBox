from __future__ import annotations

from dataclasses import dataclass


@dataclass
class LogicControlComponent:
	"""
	纯逻辑控制器（占位实现）。

	用意：
	- 用规则/脚本/有限状态机等方式控制实体，不走 LLM。
	- 例如：巡逻 NPC、自动开关门、环境系统等。
	"""

	enabled: bool = True
	provider_id: str = "logic"

