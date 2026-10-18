"""Trusted LIBERO controller. Contestant code executes in a separate process/container."""
from __future__ import annotations

import base64
import io
import json
import math
import os
import socket
import signal
import time
from pathlib import Path

from .minecraft_protocol import JsonChannel
from .wujie_scenes import LIBERO_TASKS
from .libero_scene_pack import ASSET_ID, load_scene_asset
from .libero.scoring import GoalScore, native_goals, validate_scoring

METRICS = {"task_success", "action_count", "execution_seconds", "invalid_actions",
           "completion_score", "efficiency_score", "automatic_score"}


class EvaluationInterrupted(BaseException):
    """Termination must bypass optional rendering/library exception handlers."""


def atomic_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def validate_scenarios(scenes, episodes):
    if not isinstance(scenes, list) or len(scenes) != episodes:
        raise ValueError("LIBERO 场景数量与运行配置不一致")
    identifiers = set()
    supported = {(suite, task, name) for suite, task, name, *_ in LIBERO_TASKS}
    supported_bases = {(suite, task) for suite, task, *_ in LIBERO_TASKS}
    for scene in scenes:
        if not isinstance(scene, dict):
            raise ValueError("无效的 LIBERO 任务或初始状态配置")
        private = "scene_asset" in scene
        expected = {"id", "label", "difficulty", "suite", "task_id", "task_name", "init_state_id", "seed", "max_steps"}
        if private:
            expected.add("scene_asset")
        if "scoring" in scene:
            expected.add("scoring")
            validate_scoring(scene["scoring"])
        if (set(scene) != expected
                or not isinstance(scene["id"], str) or not 1 <= len(scene["id"]) <= 64
                or scene["id"] in identifiers or not isinstance(scene["label"], str) or not 1 <= len(scene["label"]) <= 80
                or scene["difficulty"] not in {"beginner", "intermediate", "challenge"}
                or not isinstance(scene["suite"], str) or not isinstance(scene["task_name"], str)
                or type(scene["task_id"]) is not int
                or (not private and (scene["suite"], scene["task_id"], scene["task_name"]) not in supported)
                or (private and ((scene["suite"], scene["task_id"]) not in supported_bases
                    or not isinstance(scene["scene_asset"], str) or not ASSET_ID.fullmatch(scene["scene_asset"])
                    or not 1 <= len(scene["task_name"]) <= 200))
                or type(scene["init_state_id"]) is not int or not 0 <= scene["init_state_id"] < 50
                or type(scene["seed"]) is not int or not 0 <= scene["seed"] < 2**31
                or type(scene["max_steps"]) is not int or not 1 <= scene["max_steps"] <= 4000):
            raise ValueError("无效的 LIBERO 任务或初始状态配置")
        identifiers.add(scene["id"])


def checked_action(message, previous_gripper=-1):
    value = message.get("value") if message.get("type") == "action" else None
    if (not isinstance(value, list) or len(value) != 7
            or any(type(v) not in (int, float) or not math.isfinite(v) or not -1 <= v <= 1 for v in value)):
        return [0, 0, 0, 0, 0, 0, previous_gripper], False
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
    asset = load_scene_asset(scene) if "scene_asset" in scene else None
    if asset is None and task.name != scene["task_name"]:
        raise ValueError("LIBERO task catalogue differs from the frozen scene")
    bddl = str(asset[0]) if asset else suite.get_task_bddl_file_path(scene["task_id"])
    env = OffScreenRenderEnv(bddl_file_name=bddl,
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
        if asset:
            state = asset[1]
        else:
            states = torch.load(Path(get_libero_path("init_states")) / task.problem_folder / task.init_states_file,
                                map_location="cpu", weights_only=False)
            if scene["init_state_id"] >= len(states):
                raise ValueError("LIBERO initial state is unavailable")
            state = states[scene["init_state_id"]]
        observation = env.set_init_state(state)
        for _ in range(10):
            observation, _, _, _ = env.step([0, 0, 0, 0, 0, 0, -1])
        if env.check_success():
            raise ValueError("LIBERO initial state already satisfies the task")
        return env, observation, asset[2] if asset else task.language
    except BaseException:
        env.close()
        raise


def record_frame(replay, env, observation, replay_size):
    if replay.error:
        return
    if replay_size == 128:
        replay.append_observation(observation)
        return
    try:
        # Extra camera rendering never replaces the frozen policy observations.
        rendered = {name + '_image': env.sim.render(width=replay_size, height=replay_size,
                    camera_name=name, depth=False).copy()
                    for name in ('agentview', 'robot0_eye_in_hand')}
        replay.append_observation(rendered)
    except Exception as error:
        replay._failed(error)


def remaining_time(deadline, connection=None):
    if deadline is None:
        return None
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("evaluation time limit exceeded")
    if connection is not None:
        connection.settimeout(remaining)
    return remaining


def run_episode(channel, env, observation, instruction, scene, selected, evidence, deadline=None):
    from PIL import Image
    from .libero.replay import ReplayWriter
    connection = getattr(channel, "connection", None)
    remaining_time(deadline, connection)
    channel.send({"type": "reset", "goal": {"instruction": instruction,
                  "suite": scene["suite"], "task_name": scene["task_name"], "max_steps": scene["max_steps"]}})
    remaining_time(deadline, connection)
    ready = channel.receive()
    remaining_time(deadline)
    if ready != {"type": "ready"}:
        raise ValueError("policy did not acknowledge reset")
    count = invalid = 0
    valid = True
    previous_gripper = -1
    success = False
    scorer = GoalScore(validate_scoring(scene["scoring"])) if "scoring" in scene else None
    if scorer:
        initial_targets = native_goals(env, len(scorer.since))
        if any(initial_targets):
            raise ValueError("LIBERO scored targets must start incomplete")
    if {"completion_score", "efficiency_score", "automatic_score"} & set(selected) and scorer is None:
        raise ValueError("LIBERO v2 scores require frozen per-target reference steps")
    records, frames = [], []
    replay = ReplayWriter(evidence)
    started = time.monotonic()
    interruption = None
    journal = (evidence / "trajectory-steps.jsonl").open("w", encoding="utf-8", buffering=1)
    try:
        while count < scene["max_steps"] and not success:
            loop_started = time.monotonic()
            remaining_time(deadline)
            sample = public_observation(observation, count, valid)
            record_frame(replay, env, observation, 256)
            if count % 10 == 0:
                frames.append(Image.open(io.BytesIO(base64.b64decode(sample["images_jpeg_base64"]["agentview"]))).copy())
            remaining_time(deadline, connection)
            channel.send({"type": "step", "observation": sample})
            policy_started = time.monotonic()
            remaining_time(deadline, connection)
            reply = channel.receive()
            remaining_time(deadline)
            action, valid = checked_action(reply, previous_gripper)
            previous_gripper = action[-1]
            policy_seconds = time.monotonic() - policy_started
            invalid += not valid
            tick = time.monotonic()
            observation, _, _, _ = env.step(action)
            count += 1
            success = bool(env.check_success())
            if scorer:
                target_states = native_goals(env, len(scorer.since))
                if all(target_states) != success:
                    raise ValueError("LIBERO scored target predicates differ from native success")
                scorer.update(count, target_states)
            simulation_seconds = time.monotonic() - tick
            records.append({"step": count, "action": action, "valid": valid, "success": success,
                            **({"score": scorer.result(), "targets": scorer.targets()} if scorer else {}),
                            "eef_position_before": sample["eef_position"],
                            "eef_quaternion_before": sample["eef_quaternion"],
                            "gripper_position_before": sample["gripper_position"],
                            "eef_position": [float(v) for v in observation["robot0_eef_pos"]],
                            "eef_quaternion": [float(v) for v in observation["robot0_eef_quat"]],
                            "gripper_position": [float(v) for v in observation["robot0_gripper_qpos"]],
                            "policy_ms": policy_seconds * 1000,
                            "simulation_ms": simulation_seconds * 1000,
                            "controller_ms": max(0, time.monotonic() - loop_started - policy_seconds - simulation_seconds) * 1000})
            journal.write(json.dumps(records[-1], allow_nan=False) + "\n")
            if not success and count < scene["max_steps"]:
                remaining_time(deadline)
            if count % 100 == 0:
                print(f"LIBERO {scene['id']}: {count}/{scene['max_steps']} controls, {time.monotonic()-started:.1f}s", flush=True)
        elapsed = time.monotonic() - started
        final = public_observation(observation, count, valid)
        record_frame(replay, env, observation, 256)
    except BaseException as error:
        interruption = type(error).__name__
        raise
    finally:
        trace_elapsed = time.monotonic() - started
        journal.close()
        recording = replay.close()
        final = public_observation(observation, count, valid)
        frames.append(Image.open(io.BytesIO(base64.b64decode(final["images_jpeg_base64"]["agentview"]))).copy())
        if frames:
            frames[0].save(evidence / "replay.gif", save_all=True, append_images=frames[1:], duration=500, loop=0)
        atomic_json(evidence / "trajectory.json", {"task_name": scene["task_name"], "steps": records,
            "recording": recording, "status": "interrupted" if interruption else "completed",
            "interruption": interruption,
            "execution_seconds": trace_elapsed,
            **({"scoring_version": 2, "targets": scorer.targets(), "score": scorer.result()} if scorer else {})})
    values = {"task_success": 100 if success else 0, "action_count": count,
              "execution_seconds": elapsed, "invalid_actions": invalid}
    if scorer:
        values.update(scorer.result())
    return {key: values[key] for key in selected}


def evaluate(config, scenes, factory, socket_path, output):
    deadline = time.monotonic() + config["resources"]["time_seconds"]
    if config["adapter"] != "libero-agent-v1" or config["task"] != "manipulation" or set(config["metrics"]) - METRICS:
        raise ValueError("invalid LIBERO evaluation protocol")
    validate_scenarios(scenes, config["resources"]["episodes"])
    progress_path = output.parent / "partial-results.json"
    episodes = []
    interrupted_scene = None

    def checkpoint(reason=None):
        atomic_json(progress_path, {"episodes": episodes, "planned_episodes": len(scenes),
            "interrupted_scene": interrupted_scene, "reason": reason})

    checkpoint()
    unix = isinstance(socket_path, Path)
    if unix:
        socket_path.parent.mkdir(parents=True, exist_ok=True)
    elif socket_path[0] != "127.0.0.1":
        raise ValueError("practice transport must bind loopback")
    try:
        with socket.socket(socket.AF_UNIX if unix else socket.AF_INET) as listener:
            listener.bind(str(socket_path) if unix else socket_path)
            if unix:
                os.chmod(socket_path, 0o666)
            listener.listen(1)
            listener.settimeout(min(120, remaining_time(deadline)))
            with listener.accept()[0] as connection:
                remaining_time(deadline, connection)
                channel = JsonChannel(connection)
                for index, scene in enumerate(scenes):
                    interrupted_scene = {key: scene[key] for key in ("id", "label", "difficulty")}
                    checkpoint()
                    print(f"LIBERO scene {index+1}/{len(scenes)}: {scene['difficulty']} starting", flush=True)
                    evidence = output.parent / "evidence" / str(index + 1)
                    evidence.mkdir(parents=True, exist_ok=True)
                    env = None
                    setup_started = time.monotonic()
                    setup_seconds = None
                    try:
                        remaining_time(deadline)
                        env, observation, instruction = factory(scene)
                        setup_seconds = time.monotonic() - setup_started
                        remaining_time(deadline, connection)
                        metrics = run_episode(channel, env, observation, instruction, scene, config["metrics"], evidence, deadline)
                        episodes.append({"scenario": {key: scene[key] for key in ("id", "label", "difficulty")}, "metrics": metrics})
                        interrupted_scene = None
                        checkpoint()
                    finally:
                        if env is not None:
                            env.close()
                        trace_path = evidence / "trajectory.json"
                        if trace_path.is_file():
                            trace = json.loads(trace_path.read_text(encoding="utf-8"))
                            trace["setup_seconds"] = setup_seconds
                            trace["episode_wall_seconds"] = time.monotonic() - setup_started
                            atomic_json(trace_path, trace)
                connection.settimeout(5)
                channel.send({"type": "done"})
    except BaseException as error:
        reason = ("evaluation_time_limit" if isinstance(error, TimeoutError)
                  and time.monotonic() >= deadline else type(error).__name__)
        checkpoint(reason)
        raise
    temporary = output.with_suffix(".tmp")
    temporary.write_text(json.dumps({"episodes": episodes}, allow_nan=False), encoding="utf-8")
    temporary.replace(output)
    return episodes


if __name__ == "__main__":
    def interrupted(_signal, _frame):
        raise EvaluationInterrupted("trusted controller terminated")
    signal.signal(signal.SIGTERM, interrupted)
    evaluate(json.loads(Path("/input/config.json").read_text()), json.loads(Path("/scenarios/scenarios.json").read_text()),
             create_environment, Path("/ipc/agent.sock"), Path("/output/result.json"))
