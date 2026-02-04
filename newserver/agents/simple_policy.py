from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class SimplePolicyActionProvider:
	"""
	最小自动策略（用于“自动模拟闭环”先跑起来）：
	- 若看到可食用实体（tag: edible），就对它执行 Consume

	用意：把动作生成从 Manager 解耦；必要性：后续接入 LLM 时不改模拟主循环。
	"""

	def decide(self, perception: dict[str, Any], reason: str) -> list[dict[str, Any]]:
		for ent in perception.get("entities", []):
			tags = ent.get("tags", []) or []
			if "edible" in tags:
				return [{"verb": "Consume", "target_id": ent.get("id")}]
		return []

