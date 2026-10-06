"""Trusted LIBERO controller. Contestant code executes in a separate process/container."""
from __future__ import annotations

import base64
import io
import json
import math
import os
import socket
import time
from pathlib import Path

from .minecraft_protocol import JsonChannel
from .wujie_scenes import LIBERO_TASKS

METRICS = {"task_success", "action_count", "execution_seconds", "invalid_actions"}


def validate_scenarios(scenes, episodes):
    if not isinstance(scenes, list) or len(scenes) != episodes:
        raise ValueError("LIBERO 场景数量与运行配置不一致")
    identifiers = set()
    supported = {(suite, task, name) for suite, task, name, *_ in LIBERO_TASKS}
    for scene in scenes:
        if (not isinstance(scene, dict) or set(scene) != {"id", "label", "difficulty", "suite", "task_id", "task_name", "init_state_id", "seed", "max_steps"}
                or not isinstance(scene["id"], str) or not 1 <= len(scene["id"]) <= 64
                or scene["id"] in identifiers or not isinstance(scene["label"], str) or not 1 <= len(scene["label"]) <= 80
                or scene["difficulty"] not in {"beginner", "intermediate", "challenge"}
                or not isinstance(scene["suite"], str) or not isinstance(scene["task_name"], str)
                or type(scene["task_id"]) is not int
                or (scene["suite"], scene["task_id"], scene["task_name"]) not in supported
                or type(scene["init_state_id"]) is not int or not 0 <= scene["init_state_id"] < 50
                or type(scene["seed"]) is not int or not 0 <= scene["seed"] < 2**31
                or type(scene["max_steps"]) is not int or not 1 <= scene["max_steps"] <= 2000):
            raise ValueError("无效的 LIBERO 任务或初始状态配置")
        identifiers.add(scene["id"])


def checked_action(message):
    value = message.get("value") if message.get("type") == "action" else None
    if (not isinstance(value, list) or len(value) != 7
            or any(type(v) not in (int, float) or not math.isfinite(v) or not -1 <= v <= 1 for v in value)):
        return [0, 0, 0, 0, 0, 0, -1], False
    return [float(v) for v in value], True


def public_observation(observation, step, valid):
    import numpy as np
    from PIL import Image
    images = {}
    for name in ("agentview", "robot0_eye_in_hand"):
        buffer = io.BytesIO()
        # MuJoCo camera arrays have a bottom-left origin; publish upright JPEGs.
        Image.fromarray(np.asarray(observation[name + "_image"], dtype=np.uint8)[::-1]).save(buffer, "JPEG", quality=80)
        images[name] = base64.b64encode(buffer.getvalue()).decode("ascii")
    return {"images_jpeg_base64": images,
            "eef_position": [float(v) for v in observation["robot0_eef_pos"]],
            "eef_quaternion": [float(v) for v in observation["robot0_eef_quat"]],
            "gripper_position": [float(v) for v in observation["robot0_gripper_qpos"]],
            "joint_position": [float(v) for v in observation["robot0_joint_pos"]],
            "step": step, "last_action_valid": valid,
            "control": {"format": "libero-osc-pose-7", "frequency_hz": 20,
                        "gripper_close": 1, "gripper_open": -1}}


def create_environment(scene):
    import mujoco
    import torch
    from libero.libero import benchmark, get_libero_path
    from libero.libero.envs import OffScreenRenderEnv
    suite = benchmark.get_benchmark_dict()[scene["suite"]](task_order_index=0)
    task = suite.get_task(scene["task_id"])
    if task.name != scene["task_name"]:
        raise ValueError("LIBERO task catalogue differs from the frozen scene")
    env = OffScreenRenderEnv(bddl_file_name=suite.get_task_bddl_file_path(scene["task_id"]),
                            camera_heights=128, camera_widths=128, horizon=scene["max_steps"] + 10,
                            ignore_done=True, control_freq=20)
    try:
        env.seed(scene["seed"])
        env.reset()
        # Software rendering otherwise builds high-resolution shadow maps for
        # every camera. These visual effects do not change simulator physics.
        renderer = env.sim._render_context_offscreen
        renderer.scn.flags[mujoco.mjtRndFlag.mjRND_SHADOW] = 0
        renderer.scn.flags[mujoco.mjtRndFlag.mjRND_REFLECTION] = 0
        # MuJoCo's default multisampled 2048px framebuffer makes glReadPixels
        # resolve millions of unused pixels even for our 128px camera viewport.
        env.sim.model.vis.quality.offsamples = 0
        renderer.update_offscreen_size(128, 128)
        # Official trusted upstream asset only; no contestant pickle is loaded.
        states = torch.load(Path(get_libero_path("init_states")) / task.problem_folder / task.init_states_file,
                            map_location="cpu", weights_only=False)
        if scene["init_state_id"] >= len(states):
            raise ValueError("LIBERO initial state is unavailable")
        observation = env.set_init_state(states[scene["init_state_id"]])
        for _ in range(10):
            observation, _, _, _ = env.step([0, 0, 0, 0, 0, 0, -1])
        if env.check_success():
            raise ValueError("LIBERO initial state already satisfies the task")
        return env, observation, task.language
    except BaseException:
        env.close()
        raise


def run_episode(channel, env, observation, instruction, scene, selected, evidence):
    from PIL import Image
    channel.send({"type": "reset", "goal": {"instruction": instruction,
                  "suite": scene["suite"], "task_name": scene["task_name"], "max_steps": scene["max_steps"]}})
    if channel.receive() != {"type": "ready"}:
        raise ValueError("policy did not acknowledge reset")
    count = invalid = 0
    valid = True
    success = False
    records, frames = [], []
    started = time.monotonic()
    while count < scene["max_steps"] and not success:
        sample = public_observation(observation, count, valid)
        if count % 10 == 0:
            frames.append(Image.open(io.BytesIO(base64.b64decode(sample["images_jpeg_base64"]["agentview"]))).copy())
        channel.send({"type": "step", "observation": sample})
        action, valid = checked_action(channel.receive())
        invalid += not valid
        tick = time.monotonic()
        observation, _, _, _ = env.step(action)
        count += 1
        success = bool(env.check_success())
        records.append({"step": count, "action": action, "valid": valid, "success": success,
                        "eef_position": [float(v) for v in observation["robot0_eef_pos"]],
                        "simulation_ms": (time.monotonic() - tick) * 1000})
        if count % 100 == 0:
            print(f"LIBERO {scene['id']}: {count}/{scene['max_steps']} controls, {time.monotonic()-started:.1f}s", flush=True)
    elapsed = time.monotonic() - started
    final = public_observation(observation, count, valid)
    frames.append(Image.open(io.BytesIO(base64.b64decode(final["images_jpeg_base64"]["agentview"]))).copy())
    if frames:
        frames[0].save(evidence / "replay.gif", save_all=True, append_images=frames[1:], duration=500, loop=0)
    (evidence / "trajectory.json").write_text(json.dumps({"task_name": scene["task_name"], "steps": records}), encoding="utf-8")
    values = {"task_success": 100 if success else 0, "action_count": count,
              "execution_seconds": elapsed, "invalid_actions": invalid}
    return {key: values[key] for key in selected}


def evaluate(config, scenes, factory, socket_path, output):
    if config["adapter"] != "libero-agent-v1" or config["task"] != "manipulation" or set(config["metrics"]) - METRICS:
        raise ValueError("invalid LIBERO evaluation protocol")
    validate_scenarios(scenes, config["resources"]["episodes"])
    unix = isinstance(socket_path, Path)
    if unix:
        socket_path.parent.mkdir(parents=True, exist_ok=True)
    elif socket_path[0] != "127.0.0.1":
        raise ValueError("practice transport must bind loopback")
    with socket.socket(socket.AF_UNIX if unix else socket.AF_INET) as listener:
        listener.bind(str(socket_path) if unix else socket_path)
        if unix:
            os.chmod(socket_path, 0o666)
        listener.listen(1)
        listener.settimeout(120)
        with listener.accept()[0] as connection:
            connection.settimeout(config["resources"]["time_seconds"])
            channel = JsonChannel(connection)
            episodes = []
            for index, scene in enumerate(scenes):
                print(f"LIBERO scene {index+1}/{len(scenes)}: {scene['difficulty']} starting", flush=True)
                evidence = output.parent / "evidence" / str(index + 1)
                evidence.mkdir(parents=True, exist_ok=True)
                env, observation, instruction = factory(scene)
                try:
                    metrics = run_episode(channel, env, observation, instruction, scene, config["metrics"], evidence)
                    episodes.append({"scenario": {key: scene[key] for key in ("id", "label", "difficulty")}, "metrics": metrics})
                finally:
                    env.close()
            channel.send({"type": "done"})
    temporary = output.with_suffix(".tmp")
    temporary.write_text(json.dumps({"episodes": episodes}, allow_nan=False), encoding="utf-8")
    temporary.replace(output)
    return episodes


if __name__ == "__main__":
    evaluate(json.loads(Path("/input/config.json").read_text()), json.loads(Path("/scenarios/scenarios.json").read_text()),
             create_environment, Path("/ipc/agent.sock"), Path("/output/result.json"))
