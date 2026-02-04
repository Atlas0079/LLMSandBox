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
	return "\n".join(lines) if lines else "(No visible entities)"


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
	return "\n".join(lines) if lines else "(No recent interaction narrative)"


def _build_available_verbs(recipe_db: dict[str, Any], visible_entities: list[dict[str, Any]]) -> tuple[str, str, set[str]]:
	"""
	Return:
	- available_verbs_list: verb list for grounder (text)
	- available_verbs_with_duration: verb + instant/duration for planner (text)
	- allowed_verbs_set: for validation
	"""

	# Visible tag set (n)
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
		# If no target_tags, default to available; otherwise need visible entity meeting tags
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

	# For grounder: Only verb names (m)
	available_verbs_list = "\n".join([f"- {v}" for v in sorted(allowed)]) if allowed else "(No available verbs)"

	# For planner: verb + instant/duration (m)
	with_duration_lines = [f"- {v}: {verbs[v]}" for v in sorted(allowed)]
	available_verbs_with_duration = "\n".join(with_duration_lines) if with_duration_lines else "(No available verbs)"

	return (available_verbs_list, available_verbs_with_duration, allowed)


@dataclass
class LLMActionProvider:
	"""
	Two-Layer LLM Action Generator:
	- Planner: Output high-level natural language intent
	- Grounder: Output multi-step action JSON array

	Explanation:
	- Memory module not implemented yet: Planner uses perception-filtered "recent interaction narrative" directly as detailed event stream input.
	"""

	llm: DualModelLLM
	planner_template_path: Path = _repo_root() / "Data" / "LLMContext_Planner.md"
	grounder_template_path: Path = _repo_root() / "Data" / "LLMContext_Grounder.md"
	debug: bool = False

	# System Prompt Definition
	PLANNER_SYSTEM_PROMPT = """
You are a character/Agent in a sandbox world. You need to decide what to do next based on the context provided by User.

**Strong Constraints:**
- You can ONLY output "High-level natural language intent/Next goal", DO NOT output any action in `verb/target_id` format.
- Locations are discrete (location nodes). If your intent involves cross-location movement, this will be converted to a time-consuming Task at execution layer; once entering Task, you yield action rights.
- You are NOT omniscient: Can only rely on "Current Observation" and "Recent Interaction Narrative" for reasoning.

**You MUST output:**
- A short natural language intent (1-3 sentences), including: Goal, Object (if any), Location (if any), and key constraints you think of.

**You CAN output (Optional):**
- A "If failed then..." alternative strategy (Max 2).
"""

	GROUNDER_SYSTEM_PROMPT = """
You are an "Action Grounder". Your task is to translate Planner's natural language intent into concrete Action JSON sequence.

**Input:**
- Planner Intent: High-level natural language description.
- Visible Entity List: Entities you can truly manipulate at current location.
- Available Verb List: Verbs allowed to use currently.

**Output Constraints (CRITICAL):**
1. MUST output a JSON array, elements are Action objects.
2. Action object format: `{"verb": "verb", "target_id": "entityID", "parameters": {}}`
3. ONLY use verbs from "Available Verb List".
4. `target_id` MUST be in "Visible Entity List", or yourself (agent_id).
5. For duration actions, it MUST be the last action in sequence (Because it triggers Task and occupies action rights).
6. DO NOT add any Markdown tags (like ```json) around JSON, ONLY output pure JSON string.
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
		# Convention: perception can carry recipe_db (Injected by upper layer); otherwise degrade to no available verbs
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
		# Allow model output ```json fenced block```, try best effort extraction
		s = str(raw or "").strip()
		if "```" in s:
			parts = s.split("```")
			# Select the part containing '['
			for p in parts:
				if "[" in p and "]" in p:
					s = p
					break
		s = s.strip()
		# Remove possible "json" tag line
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
	Construct default two-layer LLM provider with provided model names.
	"""

	client = OpenAICompatClient()
	llm = DualModelLLM(client=client, planner_model="gemini-3-pro-preview", grounder_model="gemini-3-flash")
	debug = str(__import__("os").environ.get("DEBUG_LLM", "") or "").strip() == "1"
	return LLMActionProvider(llm=llm, debug=debug)

