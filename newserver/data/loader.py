from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class DataBundle:
	"""
	纯数据包：把模板与配方原样加载出来，供 builder 使用。
	"""

	entity_templates: dict[str, Any]
	recipes: dict[str, Any]
	world: dict[str, Any]


def load_json(path: Path) -> Any:
	with path.open("r", encoding="utf-8") as f:
		return json.load(f)


def load_data_bundle(project_root: Path) -> DataBundle:
	"""
	从 Data 目录读取 JSON。
	project_root:
	- 既可以是“仓库/Godot 工程根目录”（其下有 Data/）
	- 也可以直接传入 Data/ 目录本身
	"""

	data_dir = project_root
	if (project_root / "Data").exists():
		data_dir = project_root / "Data"
	elif str(project_root.name).lower() == "data":
		data_dir = project_root
	else:
		raise FileNotFoundError(f"Data directory not found under: {project_root}")
	entities_dir = data_dir / "Entities"

	world = load_json(data_dir / "World.json")
	recipes = load_json(data_dir / "Recipes.json")

	# 自动加载 Entities/*.json 并合并
	# 与 Godot DataManager.merge 行为一致：同名 key 后加载的覆盖先加载的
	entity_templates: dict[str, Any] = {}
	for p in sorted(list(entities_dir.glob("*.json"))):
		data = load_json(p)
		if isinstance(data, dict):
			entity_templates.update(data)

	return DataBundle(
		entity_templates=entity_templates,
		recipes=recipes,
		world=world,
	)

