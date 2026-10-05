"""Trusted MineDojo controller. Never import or execute the submitted package here."""

from __future__ import annotations

import base64
import io
import json
import os
import socket
import time
from pathlib import Path

from .minecraft_metrics import summarize_episode
from .minecraft_protocol import JsonChannel


MILESTONES = {"crafting_table", "wooden_pickaxe", "stone_pickaxe", "furnace", "iron_pickaxe", "diamond_pickaxe"}
DIFFICULTIES = {"beginner", "intermediate", "challenge"}


def inventory_items(observation: dict) -> set[str]:
    inventory = observation["inventory"]
    items = {str(name) for name, quantity in zip(inventory["name"], inventory["quantity"])
             if int(quantity) > 0 and str(name) not in {"air", "none", ""}}
    changes = observation.get("delta_inv", {})
    for source in ("inc_name_by_craft", "inc_name_by_other"):
        items.update(str(name) for name in changes.get(source, ()) if str(name) not in {"air", "none", ""})
    return items


def public_observation(observation: dict, noop_action: list[int]) -> dict:
    from PIL import Image
    import numpy as np

    rgb = np.asarray(observation["rgb"], dtype=np.uint8)
    if rgb.ndim != 3 or rgb.shape[0] != 3:
        raise ValueError("MineDojo returned an unexpected RGB frame")
    image = io.BytesIO()
    Image.fromarray(np.moveaxis(rgb, 0, -1)).save(image, format="JPEG", quality=70)
    position = [float(value) for value in observation["location_stats"]["pos"]]
    return {
        "image_jpeg_base64": base64.b64encode(image.getvalue()).decode("ascii"),
        "inventory": sorted(inventory_items(observation)),
        "position": position,
        "health": float(observation["life_stats"]["life"]),
        "noop_action": noop_action,
    }


def checked_action(message: dict, env) -> tuple[list[int], bool]:
    values = message.get("value") if message.get("type") == "action" else None
    limits = [int(value) for value in env.action_space.nvec]
    if (not isinstance(values, list) or len(values) != len(limits) or
            any(type(value) is not int or not 0 <= value < limit for value, limit in zip(values, limits))):
        return [int(value) for value in env.action_space.no_op()], False
    if not env.action_space.contains(values):
        return [int(value) for value in env.action_space.no_op()], False
    return values, True


def scenario_descriptor(scenario: dict, index: int) -> dict[str, str]:
    return {
        "id": scenario.get("id", f"episode-{index + 1}"),
        "label": scenario.get("label", f"场景 {index + 1}"),
        "difficulty": scenario.get("difficulty", "challenge"),
    }


def run_episode(channel: JsonChannel, env, scenario: dict, selected: list[str]) -> dict[str, float]:
    import numpy as np

    goals = scenario["goals"]
    observation = env.reset()
    noop = [int(value) for value in env.action_space.no_op()]
    channel.send({"type": "reset", "goal": goals})
    if channel.receive() != {"type": "ready"}:
        raise ValueError("agent did not acknowledge the scene reset")
    telemetry = {
        "required_objectives": goals, "completed_objectives": [], "collected_items": [],
        "positions": [], "tech_milestones": [], "deaths": 0, "invalid_actions": 0,
        "step_latencies_ms": [], "api_calls": 0,
    }
    seen: set[str] = set()
    for _ in range(scenario["max_steps"]):
        sample = public_observation(observation, noop)
        telemetry["positions"].append(sample["position"])
        seen.update(sample["inventory"])
        telemetry["completed_objectives"] = [goal for goal in goals if goal in seen]
        telemetry["collected_items"] = sorted(seen)
        telemetry["tech_milestones"] = sorted(seen & MILESTONES)
        if len(telemetry["completed_objectives"]) == len(goals):
            break
        started = time.perf_counter()
        channel.send({"type": "step", "observation": sample})
        action, valid = checked_action(channel.receive(), env)
        telemetry["step_latencies_ms"].append((time.perf_counter() - started) * 1000)
        if not valid:
            telemetry["invalid_actions"] += 1
        observation, _, done, _ = env.step(np.asarray(action, dtype=np.int64))
        if done or float(observation["life_stats"]["life"]) <= 0:
            if float(observation["life_stats"]["life"]) <= 0:
                telemetry["deaths"] += 1
            sample = public_observation(observation, noop)
            telemetry["positions"].append(sample["position"])
            seen.update(sample["inventory"])
            telemetry["completed_objectives"] = [goal for goal in goals if goal in seen]
            telemetry["collected_items"] = sorted(seen)
            telemetry["tech_milestones"] = sorted(seen & MILESTONES)
            break
    else:
        # The last step still produced an observation that contributes to metrics.
        sample = public_observation(observation, noop)
        telemetry["positions"].append(sample["position"])
        seen.update(sample["inventory"])
        telemetry["completed_objectives"] = [goal for goal in goals if goal in seen]
        telemetry["collected_items"] = sorted(seen)
        telemetry["tech_milestones"] = sorted(seen & MILESTONES)
    measured = summarize_episode(telemetry)
    if set(selected) - set(measured):
        raise ValueError("selected Minecraft metric has no trusted measurement")
    return {key: measured[key] for key in selected}


def validate_scenarios(scenarios, episodes):
    if not isinstance(scenarios, list) or any(not isinstance(scene, dict) for scene in scenarios):
        raise ValueError("测试场景必须是 JSON 对象列表")
    if len(scenarios) != episodes:
        raise ValueError("scenario count does not match the configured episodes")
    scenario_ids = set()
    for scene in scenarios:
        if (scene.get("task_id") != "open-ended" or type(scene.get("world_seed")) not in (int, str)
                or isinstance(scene["world_seed"], str) and not 1 <= len(scene["world_seed"]) <= 64
                or type(scene.get("max_steps")) is not int or not 1 <= scene["max_steps"] <= 3000
                or not isinstance(scene.get("goals"), list) or not scene["goals"]
                or len(scene["goals"]) > 20
                or any(not isinstance(item, str) or not 1 <= len(item) <= 160 for item in scene["goals"])
                or len(scene["goals"]) != len(set(scene["goals"]))):
            raise ValueError("invalid trusted Minecraft scenario")
        if (not isinstance(scene.get("id"), str) or not 1 <= len(scene["id"]) <= 64
                or scene["id"] in scenario_ids
                or not isinstance(scene.get("label"), str) or not 1 <= len(scene["label"]) <= 80
                or scene.get("difficulty") not in DIFFICULTIES):
            raise ValueError("Minecraft scenario needs a unique id, label and valid difficulty")
        scenario_ids.add(scene["id"])


def evaluate(config: dict, scenarios: list[dict], environment_factory, socket_path: Path | tuple, output: Path) -> list[dict]:
    if config["adapter"] != "minecraft-agent-v1" or config["task"] != "open-world":
        raise ValueError("invalid Minecraft evaluation task")
    validate_scenarios(scenarios, config["resources"]["episodes"])
    if isinstance(socket_path, Path):
        socket_path.parent.mkdir(parents=True, exist_ok=True)
    elif socket_path[0] != "127.0.0.1":
        raise ValueError("test transport must bind loopback")
    with socket.socket(socket.AF_UNIX if isinstance(socket_path, Path) else socket.AF_INET) as listener:
        listener.bind(str(socket_path) if isinstance(socket_path, Path) else socket_path)
        if isinstance(socket_path, Path):
            os.chmod(socket_path, 0o666)
        listener.listen(1)
        listener.settimeout(120)
        with listener.accept()[0] as connection:
            connection.settimeout(300)
            channel = JsonChannel(connection)
            results = []
            for index, scene in enumerate(scenarios):
                env = environment_factory(scene)
                try:
                    results.append({
                        "scenario": scenario_descriptor(scene, index),
                        "metrics": run_episode(channel, env, scene, config["metrics"]),
                    })
                finally:
                    env.close()
            channel.send({"type": "done"})
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".tmp")
    temporary.write_text(json.dumps({"episodes": results}, allow_nan=False), encoding="utf-8")
    temporary.replace(output)
    return results


def create_minedojo_environment(scene: dict):
    import minedojo

    options = {}
    # These are trusted organizer-owned scenario conditions, never contestant
    # config. They also allow controlled acceptance cases with known outcomes.
    if "initial_inventory" in scene:
        from minedojo.sim import InventoryItem
        options["initial_inventory"] = [InventoryItem(**item) for item in scene["initial_inventory"]]
    for key in ("generate_world_type", "start_time", "allow_mob_spawn", "allow_time_passage"):
        if key in scene:
            options[key] = scene[key]
    return minedojo.make(task_id=scene["task_id"], image_size=(160, 256), world_seed=scene["world_seed"], **options)


def main() -> None:
    config = json.loads(Path("/input/config.json").read_text(encoding="utf-8"))
    scenarios = json.loads(Path("/scenarios/scenarios.json").read_text(encoding="utf-8"))
    evaluate(config, scenarios, create_minedojo_environment, Path("/ipc/agent.sock"), Path("/output/result.json"))


if __name__ == "__main__":
    main()
