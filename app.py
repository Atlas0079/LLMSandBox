from __future__ import annotations

from pathlib import Path

from newserver.data.loader import load_data_bundle
from newserver.data.loader import load_json
from newserver.data.builder import build_world_state
from newserver.sim.manager import WorldManager
from newserver.sim.perception import PerceptionSystem
from newserver.interaction.engine import InteractionEngine
from newserver.executor.executor import WorldExecutor
from newserver.agents.simple_policy import SimplePolicyActionProvider
from newserver.agents.llm_action_provider import build_default_llm_provider



def main() -> None:
	# If you want to load from an external directory in the future, just change project_root to the external path.
	project_root = Path(__file__).resolve().parent

	bundle = load_data_bundle(project_root)

	# Optional: Switch test world (default World.json)
	# Example: $env:WORLD_JSON="World_LLM_Demo.json"
	world_json_name = str(__import__("os").environ.get("WORLD_JSON", "") or "").strip()
	if world_json_name:
		world_path = project_root / "Data" / world_json_name
		bundle.world = load_json(world_path)
	result = build_world_state(bundle.world, bundle.entity_templates)

	ws = result.world_state
	print("World loaded.")
	print("Time:", ws.game_time.time_to_string(), "ticks=", ws.game_time.total_ticks)
	print("Locations:", list(ws.locations.keys()))
	print("Entities:", list(ws.entities.keys()))

	# Example: Output beatrice_01's location and visible entities
	agent_id = "beatrice_01"
	loc = ws.get_location_of_entity(agent_id)
	print("Agent location:", loc.location_id if loc else None)

	# Task progression regression test (Sleep 60 ticks)
	agent = ws.get_entity_by_id(agent_id)
	worker = agent.get_component("WorkerComponent") if agent else None
	current_task_id = getattr(worker, "current_task_id", "") if worker else ""
	print("Agent current_task_id:", current_task_id)
	if current_task_id:
		task = ws.get_task_by_id(current_task_id)
		if task:
			print("Task loaded:", task.task_id, task.task_type, "progress=", task.progress, "/", task.required_progress, "progressor=", task.progressor_id or "<default>")

	# Print perception results once to confirm if container hiding is effective
	perception = PerceptionSystem().perceive(ws, agent_id)
	print("Perception visible entity ids:", [e.get("id") for e in perception.get("entities", [])])
	print("Perception hidden_entity_count:", perception.get("hidden_entity_count"))

	# Optional: Continuous task regression test (avoid interfering with LLM demo)
	# Example: $env:DEMO_DURATION_TEST="1"
	if str(__import__("os").environ.get("DEMO_DURATION_TEST", "") or "").strip() == "1":
		if worker is not None:
			worker.stop_task()
		print("After stop_task, current_task_id:", getattr(worker, "current_task_id", "") if worker else "")
		sleep_result = InteractionEngine(recipe_db=bundle.recipes).process_command(ws, agent_id, {"verb": "Sleep", "target_id": agent_id})
		print("Sleep command result:", {"status": sleep_result.get("status"), "reason": sleep_result.get("reason"), "message": sleep_result.get("message")})
		if sleep_result.get("status") == "success":
			for effect in sleep_result.get("effects", []):
				WorldExecutor(entity_templates=bundle.entity_templates).execute(ws, effect, sleep_result.get("context", {}) or {})
		print("After Sleep command, current_task_id:", getattr(worker, "current_task_id", "") if worker else "")

	# You can switch to two-layer LLM control (planner+grounder) via environment variable USE_LLM=1.
	use_llm = str(__import__("os").environ.get("USE_LLM", "") or "").strip() == "1"
	action_provider = build_default_llm_provider() if use_llm else SimplePolicyActionProvider()
	max_ticks_env = str(__import__("os").environ.get("MAX_TICKS", "") or "").strip()
	max_ticks = int(max_ticks_env) if max_ticks_env else (15 if use_llm else 65)
	manager = WorldManager(
		world_state=ws,
		interaction_engine=InteractionEngine(recipe_db=bundle.recipes),
		executor=WorldExecutor(entity_templates=bundle.entity_templates),
		perception_system=PerceptionSystem(),
		action_provider=action_provider,
	)
	events = manager.run(max_ticks=max_ticks)
	print("Events (filtered):")
	verbose_events = str(__import__("os").environ.get("VERBOSE_EVENTS", "") or "").strip() == "1"
	for e in events:
		if not verbose_events:
			# Only print events of interest to avoid flooding the screen
			if e.get("type") in ["TickAdvanced", "TaskFinished", "DecisionCycleAborted", "ActionFailed", "ExecutorError"]:
				print("  ", e)
		# If verbose_events=1, they are already printed in real-time in the manager, so no repetition here

	# LLM demo: Print a segment of "interactive narrative" to make behavior look more intuitive
	if use_llm:
		ps = PerceptionSystem()
		interactions = ps.get_visible_interactions(ws, agent_id, max_records=20, tick_window=99999)
		print("\nRecent interactions (rendered):")
		for it in interactions:
			print("  ", it.get("text"))

	# Finally check if the task has been cleaned up
	if current_task_id:
		task_after = ws.get_task_by_id(current_task_id)
		print("Task after run:", task_after)


if __name__ == "__main__":
	main()

