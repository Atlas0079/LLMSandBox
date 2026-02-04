from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ContainerSlot:
	"""
	对齐 Godot `ContainerComponent.gd` 的 slot 结构：config + items（ID 列表）。
	"""

	config: dict[str, Any] = field(default_factory=dict)
	items: list[str] = field(default_factory=list)


@dataclass
class ContainerComponent:
	slots: dict[str, ContainerSlot] = field(default_factory=dict)

	def get_all_item_ids(self) -> list[str]:
		all_ids: list[str] = []
		for slot in self.slots.values():
			all_ids.extend(list(slot.items))
		return all_ids

	def has_item_id(self, item_id: str) -> bool:
		for slot in self.slots.values():
			if item_id in slot.items:
				return True
		return False

	def remove_entity_by_id(self, item_id: str) -> bool:
		for slot in self.slots.values():
			if item_id in slot.items:
				slot.items.remove(item_id)
				return True
		return False

	def add_entity(self, item_entity: Any, target_slot_id: str = "") -> bool:
		"""
		最小版容器加入逻辑（对齐 Godot 的 ContainerComponent.add_entity 的“形状”）。

		注意：
		- 这里没有实现“循环嵌套检测”（需要访问 WorldState 的后代收集）。
		  假设存在：CycleDetector / WorldState.collect_descendant_item_ids
		  用意：避免 A 包含 B 且 B 包含 A；必要性：复杂容器系统里必须有，否则会死循环/破坏索引。
		"""
		if item_entity is None:
			return False
		item_id = str(getattr(item_entity, "entity_id", ""))
		if not item_id:
			return False

		if self.has_item_id(item_id):
			return False

		slot = None
		if target_slot_id:
			slot = self.slots.get(str(target_slot_id))
		if slot is None:
			slot = self._find_first_available_slot_for(item_entity)
		if slot is None:
			return False

		slot.items.append(item_id)
		return True

	def _find_first_available_slot_for(self, item_entity: Any):
		"""
		选择第一个可用槽位：检查 capacity_count 与 accepted_tags（最小实现）。
		"""
		item_tags = []
		if hasattr(item_entity, "get_all_tags"):
			try:
				item_tags = list(item_entity.get_all_tags())
			except Exception:
				item_tags = []

		for slot in self.slots.values():
			cfg = slot.config or {}
			cap_count = int(cfg.get("capacity_count", 999))
			if len(slot.items) >= cap_count:
				continue

			accepted = list(cfg.get("accepted_tags", []) or [])
			if accepted:
				ok = True
				for t in accepted:
					if str(t) not in item_tags:
						ok = False
						break
				if not ok:
					continue

			return slot

		return None

