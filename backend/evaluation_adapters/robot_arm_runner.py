"""Trusted robosuite / MuJoCo controller; never executes contestant Python."""
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

TASKS = {"lift", "place", "stack"}
METRICS = {"task_success", "stable_grasps", "recovery_success", "action_count",
           "execution_seconds", "invalid_actions", "stable_seconds"}


def vector(value, length, low, high):
    return (isinstance(value, list) and len(value) == length and all(
        type(v) in (int, float) and math.isfinite(v) and low <= v <= high for v in value))


def validate_scenarios(scenes, episodes):
    if not isinstance(scenes, list) or len(scenes) != episodes:
        raise ValueError("机械臂场景数量必须与测试场景数一致。")
    ids = set()
    required = {"id", "label", "difficulty", "task", "seed", "max_steps", "hold_steps"}
    for scene in scenes:
        if not isinstance(scene, dict) or not required <= set(scene) or set(scene) - required - {"target", "placements"}:
            raise ValueError("机械臂场景字段不正确。")
        if (not isinstance(scene["id"], str) or not 1 <= len(scene["id"]) <= 64 or scene["id"] in ids
                or not isinstance(scene["label"], str) or not 1 <= len(scene["label"]) <= 80
                or not isinstance(scene["difficulty"], str) or scene["difficulty"] not in {"beginner", "intermediate", "challenge"}
                or not isinstance(scene["task"], str) or scene["task"] not in TASKS or type(scene["seed"]) is not int or not 0 <= scene["seed"] <= 2**31-1
                or type(scene["max_steps"]) is not int or not 10 <= scene["max_steps"] <= 3000
                or type(scene["hold_steps"]) is not int or not 5 <= scene["hold_steps"] <= min(100, scene["max_steps"])):
            raise ValueError("机械臂场景须有唯一编号、有效任务、种子和步数限制。")
        if scene["task"] == "place" and not vector(scene.get("target"), 2, -0.25, 0.25):
            raise ValueError("放置任务须设置桌面内的目标坐标。")
        if "target" in scene and scene["task"] != "place":
            raise ValueError("只有放置任务接受目标坐标。")
        if "placements" in scene:
            placements = scene["placements"]
            if (not isinstance(placements, dict) or set(placements) != {"red", "green"}
                    or any(not vector(p, 2, -0.15, 0.15) for p in placements.values())
                    or math.dist(placements["red"], placements["green"]) < 0.08):
                raise ValueError("初始积木位置须在桌面范围内，且相隔至少 8 cm。")
        ids.add(scene["id"])


def checked_command(message):
    value = message.get("value") if message.get("type") == "action" else None
    if not isinstance(value, dict):
        return None
    repeat = value.get("repeat", 1)
    if type(repeat) is not int or not 1 <= repeat <= 20:
        return None
    if set(value) <= {"delta", "repeat"} and vector(value.get("delta"), 7, -1, 1):
        return {"delta": value["delta"], "repeat": repeat}
    if (set(value) <= {"target", "gripper", "repeat"} and vector(value.get("target"), 3, -0.35, 1.3)
            and -0.35 <= value["target"][0] <= 0.35 and -0.35 <= value["target"][1] <= 0.35
            and 0.80 <= value["target"][2] <= 1.3
            and type(value.get("gripper")) in (int, float) and value["gripper"] in (-1, 1)):
        return {"target": value["target"], "gripper": value["gripper"], "repeat": repeat}
    return None


def public_observation(observation, step, valid, env):
    import numpy as np
    from PIL import Image
    cameras = {}
    calibration = {}
    for name in ("agentview", "robot0_eye_in_hand"):
        buffer = io.BytesIO()
        Image.fromarray(np.asarray(observation[name + "_image"], dtype=np.uint8)[::-1]).save(buffer, "JPEG", quality=75)
        cameras[name] = base64.b64encode(buffer.getvalue()).decode("ascii")
        camera_id = env.sim.model.camera_name2id(name)
        height, width = observation[name + "_image"].shape[:2]
        focal = .5 * height / np.tan(env.sim.model.cam_fovy[camera_id] * np.pi / 360)
        transform = np.eye(4)
        transform[:3, :3] = env.sim.data.cam_xmat[camera_id].reshape(3, 3) @ np.diag([1, -1, -1])
        transform[:3, 3] = env.sim.data.cam_xpos[camera_id]
        calibration[name] = {"intrinsic": [[float(focal), 0, width / 2], [0, float(focal), height / 2], [0, 0, 1]],
                             "camera_to_world": transform.tolist()}
    return {"images_jpeg_base64": cameras, "camera_calibration": calibration,
            "eef_position": [float(v) for v in observation["robot0_eef_pos"]],
            "eef_quaternion": [float(v) for v in observation["robot0_eef_quat"]],
            "gripper_position": [float(v) for v in observation["robot0_gripper_qpos"]],
            "step": step, "last_action_valid": valid,
            "control": {"frequency_hz": 20, "delta_size": 7, "max_repeat": 20, "close_gripper": 1}}


class PhysicalVerifier:
    """Metrics come only from trusted physical measurements, never agent claims."""
    def __init__(self, scene):
        self.scene = scene
        self.hold = self.longest = self.grasp_ticks = self.grasp_attempts = self.stable_grasps = 0
        self.drops = self.recoveries = 0
        self.was_grasped = self.ever_lifted = self.recovery_pending = self.ever_success = False
        self.command_count = self.invalid = self.steps = 0

    def update(self, red, green, grasped, contact, gripper_closed=True):
        self.steps += 1
        lifted = red[2] > 0.86
        self.ever_lifted |= lifted and grasped
        if grasped:
            if not self.was_grasped:
                self.grasp_attempts += 1
            self.grasp_ticks += 1
            if self.grasp_ticks == self.scene["hold_steps"]:
                self.stable_grasps += 1
        else:
            if self.was_grasped and lifted and gripper_closed:
                self.drops += 1
                self.recovery_pending = True
            self.grasp_ticks = 0
        task = self.scene["task"]
        if task == "lift":
            success = lifted and grasped
        elif task == "stack":
            success = (self.ever_lifted and not grasped and contact
                       and math.dist(red[:2], green[:2]) < 0.025 and 0.035 < red[2] - green[2] < 0.055)
        else:
            success = (self.ever_lifted and not grasped and math.dist(red[:2], self.scene["target"]) < 0.035
                       and 0.813 < red[2] < 0.835)
        self.hold = self.hold + 1 if success else 0
        self.longest = max(self.longest, self.hold)
        self.ever_success |= self.hold >= self.scene["hold_steps"]
        if self.recovery_pending and grasped and self.grasp_ticks == self.scene["hold_steps"]:
            self.recoveries += 1
            self.recovery_pending = False
        self.was_grasped = grasped

    def metrics(self, elapsed):
        return {"task_success": 100 if self.ever_success else 0,
                "stable_grasps": 100 * self.stable_grasps / max(1, self.grasp_attempts),
                "recovery_success": 100 * self.recoveries / max(1, self.drops),
                "action_count": self.command_count, "execution_seconds": elapsed,
                "invalid_actions": self.invalid, "stable_seconds": self.longest / 20}


def create_environment(scene):
    import robosuite
    from robosuite.controllers import load_composite_controller_config
    controller = load_composite_controller_config(controller="BASIC")
    # Seven normalized controls: dx, dy, dz, rotation axis-angle, gripper.
    controller["body_parts"]["right"]["type"] = "OSC_POSE"
    return robosuite.make("Stack", robots="Panda", controller_configs=controller,
        seed=scene["seed"], initialization_noise=None, use_object_obs=False,
        has_renderer=False, has_offscreen_renderer=True, use_camera_obs=True,
        camera_names=["agentview", "robot0_eye_in_hand"], camera_heights=160, camera_widths=160,
        control_freq=20, horizon=scene["max_steps"], reward_shaping=False)


def run_episode(channel, env, scene, selected, evidence):
    import numpy as np
    observation = env.reset()
    if scene.get("placements"):
        for obj, key, height in ((env.cubeA, "red", .821), (env.cubeB, "green", .826)):
            env.sim.data.set_joint_qpos(obj.joints[0], np.array([*scene["placements"][key], height, 1, 0, 0, 0]))
        env.sim.forward()
        observation = env._get_observations(force_update=True)
    goal = {"task": scene["task"], "instruction": {"lift": "抓住红色积木并抬离桌面，保持稳定。",
        "place": "将红色积木放到目标位置，松开夹爪并保持稳定。", "stack": "将红色积木堆叠到绿色积木上，松开夹爪并保持稳定。"}[scene["task"]]}
    if scene["task"] == "place":
        goal["target_xy"] = scene["target"]
    channel.send({"type": "reset", "goal": goal})
    if channel.receive() != {"type": "ready"}:
        raise ValueError("agent did not acknowledge reset")
    verifier = PhysicalVerifier(scene)
    started = time.monotonic()
    valid = True
    records = []
    while verifier.steps < scene["max_steps"] and not verifier.ever_success:
        sample = public_observation(observation, verifier.steps, valid, env)
        # Bounded, controller-owned images and trajectory; no contestant filesystem.
        for name, data in sample["images_jpeg_base64"].items():
            (evidence / f"{verifier.command_count:04d}-{name}.jpg").write_bytes(base64.b64decode(data))
        channel.send({"type": "step", "observation": sample})
        command = checked_command(channel.receive())
        valid = command is not None
        verifier.command_count += 1
        verifier.invalid += not valid
        command = command or {"delta": [0, 0, 0, 0, 0, 0, -1], "repeat": 1}
        for _ in range(min(command["repeat"], scene["max_steps"] - verifier.steps)):
            if "target" in command:
                delta = np.clip((np.asarray(command["target"]) - observation["robot0_eef_pos"]) / .05, -1, 1)
                action = np.array([*delta, 0, 0, 0, command["gripper"]])
            else:
                action = np.asarray(command["delta"], dtype=float)
            observation, _, done, _ = env.step(action)
            red = env.sim.data.body_xpos[env.cubeA_body_id].copy().tolist()
            green = env.sim.data.body_xpos[env.cubeB_body_id].copy().tolist()
            grasped = bool(env._check_grasp(gripper=env.robots[0].gripper, object_geoms=env.cubeA))
            contact = bool(env.check_contact(env.cubeA, env.cubeB))
            verifier.update(red, green, grasped, contact, bool(action[-1] > 0))
            records.append({"step": verifier.steps, "red": red, "green": green,
                "grasped": grasped, "contact": contact, "success_hold": verifier.hold})
            if done or verifier.ever_success:
                break
    (evidence / "trajectory.json").write_text(json.dumps(records), encoding="utf-8")
    measured = verifier.metrics(time.monotonic() - started)
    return {key: measured[key] for key in selected}


def evaluate(config, scenes, factory, socket_path, output):
    if config["adapter"] != "robot-arm-agent-v1" or config["task"] not in {"pick-place", "multi-step"}:
        raise ValueError("unknown robot arm adapter")
    validate_scenarios(scenes, config["resources"]["episodes"])
    if set(config["metrics"]) - METRICS:
        raise ValueError("unknown robot arm metric")
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    with socket.socket(socket.AF_UNIX) as listener:
        listener.bind(str(socket_path))
        os.chmod(socket_path, 0o666)
        listener.listen(1)
        listener.settimeout(120)
        with listener.accept()[0] as connection:
            connection.settimeout(config["resources"]["time_seconds"])
            channel = JsonChannel(connection)
            episodes = []
            for scene in scenes:
                # Validated IDs cannot become paths; keep storage indexed independently.
                evidence = output.parent / "evidence" / str(len(episodes) + 1)
                evidence.mkdir(parents=True, exist_ok=True)
                env = factory(scene)
                try:
                    metrics = run_episode(channel, env, scene, config["metrics"], evidence)
                    episodes.append({"scenario": {key: scene[key] for key in ("id", "label", "difficulty")}, "metrics": metrics})
                finally:
                    env.close()
            channel.send({"type": "done"})
    output.write_text(json.dumps({"episodes": episodes}, allow_nan=False), encoding="utf-8")
    return episodes


def main():
    config = json.loads(Path("/input/config.json").read_text())
    scenes = json.loads(Path("/scenarios/scenarios.json").read_text())
    evaluate(config, scenes, create_environment, Path("/ipc/agent.sock"), Path("/output/result.json"))


if __name__ == "__main__":
    main()
