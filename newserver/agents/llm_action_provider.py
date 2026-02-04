from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..llm.openai_compat_client import DualModelLLM, OpenAICompatClient


def _repo_root() -> Path:
	# newserver/agents/llm_action_provider.py -> repo root
	return Path(__file__).resolve().parents[2]


def _read_text(path: Path) -> str:
	return path.read_text(encoding="utf-8")


def _fill_template(template: str, mapping: dict[str, Any]) -> str:
	out = str(template)
	for k, v in (mapping or {}).items():
		out = out.replace(f"{{{{{k}}}}}", str(v))
	return out


def _entities_table(entities: list[dict[str, Any]]) -> str:
	lines: list[str] = []
	for e in list(entities or []):
		if not isinstance(e, dict):
			continue
		eid = str(e.get("id", "") or "")
		name = str(e.get("name", "") or "")
		tags = e.get("tags", []) or []
		lines.append(f"- id: {eid}, name: {name}, tags: {list(tags)}")
	return "\n".join(lines) if lines else "(无可见实体)"


def _interactions_text(interactions: list[dict[str, Any]]) -> str:
	lines: list[str] = []
	for it in list(interactions or []):
		if not isinstance(it, dict):
			continue
		tick = it.get("tick", None)
		text = str(it.get("text", "") or "")
		if tick is None:
			lines.append(f"- {text}")
		else:
			lines.append(f"- [tick {int(tick)}] {text}")
	return "\n".join(lines) if lines else "(无近期交互叙事)"


def _build_available_verbs(recipe_db: dict[str, Any], visible_entities: list[dict[str, Any]]) -> tuple[str, str, set[str]]:
	"""
	返回：
	- available_verbs_list: 给 grounder 的 verb 列表（文本）
	- available_verbs_with_duration: 给 planner 的 verb + instant/duration（文本）
	- allowed_verbs_set: 用于校验
	"""

	# 可见 tag 集合（n）
	visible_tags: set[str] = set()
	for e in list(visible_entities or []):
		tags = (e or {}).get("tags", []) or []
		for t in list(tags):
			visible_tags.add(str(t))

	verbs: dict[str, str] = {}  # verb -> "instant"/"duration"
	for _rid, recipe in (recipe_db or {}).items():
		if not isinstance(recipe, dict):
			continue
		verb = str(recipe.get("verb", "") or "").strip()
		if not verb:
			continue
		req_tags = list(recipe.get("target_tags", []) or [])
		# 若没有 target_tags，默认认为可用；否则需要可见实体中存在满足 tags 的候选
		ok = True
		if req_tags:
			ok = True
			for tag in req_tags:
				if str(tag) not in visible_tags:
					ok = False
					break
		if not ok:
			continue
		process = recipe.get("process", {}) or {}
		required_progress = float((process or {}).get("required_progress", 0) or 0)
		verbs[verb] = "duration" if required_progress != 0 else "instant"

	allowed = set(verbs.keys())

	# grounder 用：只给 verb 名字（m）
	available_verbs_list = "\n".join([f"- {v}" for v in sorted(allowed)]) if allowed else "(无可用动词)"

	# planner 用：verb + instant/duration（m）
	with_duration_lines = [f"- {v}: {verbs[v]}" for v in sorted(allowed)]
	available_verbs_with_duration = "\n".join(with_duration_lines) if with_duration_lines else "(无可用动词)"

	return (available_verbs_list, available_verbs_with_duration, allowed)


@dataclass
class LLMActionProvider:
	"""
	两层 LLM 的 action 生成器：
	- Planner：输出高层自然语言意图
	- Grounder：输出多步 action JSON 数组

	说明：
	- 记忆模块暂不实现：Planner 直接使用感知过滤后的“最近交互叙事”作为详细事件流输入。
	"""

	llm: DualModelLLM
	planner_template_path: Path = _repo_root() / "Data" / "LLMContext_Planner.md"
	grounder_template_path: Path = _repo_root() / "Data" / "LLMContext_Grounder.md"
	debug: bool = False

	# System Prompt 定义
	PLANNER_SYSTEM_PROMPT = """
你是沙盒世界中的角色/智能体（Agent）。你需要基于 User 提供的上下文，决定接下来要做什么。

**强约束：**
- 你只能输出“高层自然语言意图/下一步目标”，不要输出任何 `verb/target_id` 形式的 action。
- 地点是离散的（location 节点）。若你的意图包含跨地点移动，这会在执行层被转为一个需要时间推进的 Task；一旦进入 Task，你将交还行动权。
- 你并不全知：只能依赖“当前观测”和“最近交互叙事”推理。

**你必须输出：**
- 一段简短的自然语言意图（1-3 句），包含：目标、对象（若有）、地点（若有）、以及你认为的关键约束。

**你可以输出（可选）：**
- 一段“如果失败则…”的备选策略（最多 2 条）。
"""

	GROUNDER_SYSTEM_PROMPT = """
你是一个“动作翻译官”（Action Grounder）。你的任务是把 Planner 的自然语言意图翻译成具体的 Action JSON 序列。

**输入：**
- Planner 意图：高层的自然语言描述。
- 可见实体列表：当前地点你真正能操作的实体。
- 可用动词列表：当前允许使用的动词。

**输出约束（CRITICAL）：**
1. 必须输出一个 JSON 数组，数组元素是 Action 对象。
2. Action 对象格式：`{"verb": "动词", "target_id": "实体ID", "parameters": {}}`
3. 只能使用“可用动词列表”里的动词。
4. `target_id` 必须在“可见实体列表”里，或者是你自己（agent_id）。
5. 对于耗时动作（duration），它必须是序列的最后一个动作（因为它会触发 Task 占用行动权）。
6. 不要在 JSON 前后加任何 Markdown 标记（如 ```json），只输出纯 JSON 字符串。
"""

	def decide(self, perception: dict[str, Any], reason: str, agent_id: str | None = None) -> list[dict[str, Any]]:
		debug_prompts = str(__import__("os").environ.get("LLM_DEBUG_PROMPTS", "") or "").strip() == "1"
		agent_id = str(agent_id or perception.get("agent_id", "") or "")
		visible_entities = list((perception or {}).get("entities", []) or [])
		interactions = list((perception or {}).get("interactions", []) or [])
		loc = (perception or {}).get("location", {}) or {}
		loc_id = str((loc or {}).get("id", "") or "")
		loc_name = str((loc or {}).get("name", "") or "")
		tick = (perception or {}).get("tick", None)
		tick_str = str(tick) if tick is not None else ""

		recipe_db: dict[str, Any] = {}
		# 约定：perception 可以携带 recipe_db（由上层注入）；否则退化为无可用动词
		if isinstance((perception or {}).get("recipe_db", None), dict):
			recipe_db = dict((perception or {}).get("recipe_db") or {})

		available_verbs_list, available_verbs_with_duration, allowed_verbs = _build_available_verbs(recipe_db, visible_entities)

		planner_template = _read_text(self.planner_template_path)
		planner_prompt = _fill_template(
			planner_template,
			{
				"agent_name": str((perception or {}).get("agent_name", "") or agent_id),
				"personality_summary": str((perception or {}).get("personality_summary", "") or ""),
				"common_knowledge_summary": str((perception or {}).get("common_knowledge_summary", "") or ""),
				"long_term_memory": "",
				"mid_term_summary": "",
				"current_goal": "",
				"current_plan": "",
				"current_task_id": str((perception or {}).get("current_task_id", "") or ""),
				"tick": tick_str,
				"location_id": loc_id,
				"location_name": loc_name,
				"available_verbs_with_duration": available_verbs_with_duration,
				"visible_entities_table": _entities_table(visible_entities),
				"recent_interactions_text": _interactions_text(interactions),
				"last_failure_summary": str(reason or ""),
				"planner_output_here": "",
			},
		)

		if bool(self.debug) and bool(debug_prompts):
			print("\n[LLM][Planner] system prompt:")
			print(self.PLANNER_SYSTEM_PROMPT.strip())
			print("\n[LLM][Planner] user prompt:")
			print(planner_prompt)

		intent = self.llm.planner_text(
			messages=[
				{"role": "system", "content": self.PLANNER_SYSTEM_PROMPT},
				{"role": "user", "content": planner_prompt},
			],
			temperature=0.4,
		).strip()
		if bool(self.debug):
			print("\n[LLM][Planner] intent:")
			print(intent)

		grounder_template = _read_text(self.grounder_template_path)
		grounder_prompt = _fill_template(
			grounder_template,
			{
				"planner_intent_text": intent,
				"tick": tick_str,
				"location_id": loc_id,
				"location_name": loc_name,
				"visible_entities_table": _entities_table(visible_entities),
				"available_verbs_list": available_verbs_list,
				"recent_interactions_text": _interactions_text(interactions),
				"verb": "",
				"target_id": "",
			},
		)

		if bool(self.debug) and bool(debug_prompts):
			print("\n[LLM][Grounder] system prompt:")
			print(self.GROUNDER_SYSTEM_PROMPT.strip())
			print("\n[LLM][Grounder] user prompt:")
			print(grounder_prompt)

		raw = self.llm.grounder_text(
			messages=[
				{"role": "system", "content": self.GROUNDER_SYSTEM_PROMPT},
				{"role": "user", "content": grounder_prompt},
			],
			temperature=0.2,
		).strip()
		if bool(self.debug):
			print("\n[LLM][Grounder] raw actions:")
			print(raw)

		actions = self._parse_actions(raw)
		if bool(self.debug):
			print("\n[LLM][Grounder] parsed actions:")
			print(actions)
		return self._validate_actions(actions, allowed_verbs, visible_entities)

	def _parse_actions(self, raw: str) -> list[dict[str, Any]]:
		# 允许模型输出 ```json fenced block```，尽量容错提取
		s = str(raw or "").strip()
		if "```" in s:
			parts = s.split("```")
			# 选择包含 '[' 的那段
			for p in parts:
				if "[" in p and "]" in p:
					s = p
					break
		s = s.strip()
		# 去掉可能的 "json" 标记行
		if s.lower().startswith("json"):
			s = "\n".join(s.splitlines()[1:]).strip()

		try:
			data = json.loads(s)
		except Exception:
			return []
		if not isinstance(data, list):
			return []
		out: list[dict[str, Any]] = []
		for item in data:
			if isinstance(item, dict):
				out.append(dict(item))
		return out

	def _validate_actions(self, actions: list[dict[str, Any]], allowed_verbs: set[str], visible_entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
		visible_ids: set[str] = set()
		for e in list(visible_entities or []):
			if isinstance(e, dict):
				visible_ids.add(str(e.get("id", "") or ""))

		valid: list[dict[str, Any]] = []
		for a in list(actions or []):
			verb = str((a or {}).get("verb", "") or "").strip()
			target_id = str((a or {}).get("target_id", "") or "").strip()
			if not verb or verb not in allowed_verbs:
				continue
			if not target_id or target_id not in visible_ids:
				continue
			params = (a or {}).get("parameters", {}) or {}
			if not isinstance(params, dict):
				params = {}
			valid.append({"verb": verb, "target_id": target_id, "parameters": dict(params)})
		return valid


def build_default_llm_provider() -> LLMActionProvider:
	"""
	按你提供的模型名构造默认两层 LLM provider。
	"""

	client = OpenAICompatClient()
	llm = DualModelLLM(client=client, planner_model="gemini-3-pro-preview", grounder_model="gemini-3-flash")
	debug = str(__import__("os").environ.get("DEBUG_LLM", "") or "").strip() == "1"
	return LLMActionProvider(llm=llm, debug=debug)

