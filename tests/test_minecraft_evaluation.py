from __future__ import annotations

import json
import socket
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from evaluation_adapters.minecraft_agent import load_agent, run as run_agent  # noqa: E402
from evaluation_adapters.minecraft_protocol import JsonChannel  # noqa: E402
from evaluation_adapters.minecraft_runner import evaluate  # noqa: E402


class FakeActionSpace:
    nvec = [3, 3, 4, 25, 25, 8, 244, 36]

    def no_op(self):
        return [0] * 8

    def contains(self, action):
        return all(0 <= value < limit for value, limit in zip(action, self.nvec))


class FakeMinecraft:
    action_space = FakeActionSpace()

    def __init__(self, scene):
        self.scene = scene
        self.step_count = 0
        self.closed = False

    def observe(self):
        items = self.scene["goals"][:self.step_count]
        return {
            "rgb": np.zeros((3, 8, 8), dtype=np.uint8),
            "inventory": {"name": items, "quantity": [1] * len(items)},
            "location_stats": {"pos": [self.step_count * 3, 0, self.step_count * 4]},
            "life_stats": {"life": 20},
        }

    def reset(self):
        self.step_count = 0
        return self.observe()

    def step(self, action):
        if int(action[0]) == 1:
            self.step_count += 1
        return self.observe(), 0, False, {}

    def close(self):
        self.closed = True


class MinecraftEvaluationTests(unittest.TestCase):
    def test_zip_agent_runs_three_scenes_and_controller_writes_metrics(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "submission.zip"
            source = Path(__file__).resolve().parents[1] / "backend" / "evaluation_adapters" / "minecraft" / "example_agent.py"
            with zipfile.ZipFile(package, "w") as archive:
                archive.write(source, "agent.py")
            agent = load_agent(package, root / "agent")
            scenarios = [
                {
                    "id": identifier, "label": label, "difficulty": difficulty,
                    "task_id": "open-ended", "world_seed": seed, "max_steps": 5, "goals": goals,
                }
                for identifier, label, difficulty, seed, goals in (
                    ("woodcraft", "入门：从零开始", "beginner", 1, ["log"]),
                    ("stone-tools", "进阶：石器工具链", "intermediate", 2, ["wooden_pickaxe", "stone_pickaxe"]),
                    ("iron-age", "挑战：铁器时代", "challenge", 3, ["iron_ore", "iron_ingot", "iron_pickaxe"]),
                )
            ]
            config = {
                "adapter": "minecraft-agent-v1", "task": "open-world",
                "resources": {"episodes": 3},
                "metrics": ["task_success", "exploration_progress", "objectives", "distance_blocks"],
            }
            environments = []
            errors = []
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                address = probe.getsockname()

            def controller():
                try:
                    evaluate(config, scenarios, lambda scene: environments.append(FakeMinecraft(scene)) or environments[-1],
                             address, root / "result.json")
                except Exception as error:
                    errors.append(error)

            thread = threading.Thread(target=controller)
            thread.start()
            try:
                with socket.socket() as connection:
                    for _ in range(100):
                        try:
                            connection.connect(address)
                            break
                        except ConnectionRefusedError:
                            threading.Event().wait(0.02)
                    else:
                        self.fail("test controller did not start")
                    connection.settimeout(5)
                    run_agent(agent, JsonChannel(connection))
            finally:
                thread.join(timeout=10)
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])
            self.assertTrue(all(env.closed for env in environments))
            results = json.loads((root / "result.json").read_text(encoding="utf-8"))["episodes"]
            self.assertEqual([item["scenario"]["difficulty"] for item in results], ["beginner", "intermediate", "challenge"])
            self.assertEqual([item["metrics"]["task_success"] for item in results], [100, 100, 100])
            self.assertEqual([item["metrics"]["distance_blocks"] for item in results], [5, 10, 15])

    def test_private_scenario_file_has_three_difficulty_levels(self):
        path = Path(__file__).resolve().parents[1] / "backend" / "data" / "minecraft-scenarios.json"
        if not path.is_file():
            self.skipTest("private local scenario file is not distributed with the repository")
        scenarios = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual([scene["difficulty"] for scene in scenarios], ["beginner", "intermediate", "challenge"])
        self.assertEqual([scene["max_steps"] for scene in scenarios], [600, 1500, 3000])
        self.assertEqual(scenarios[2]["goals"], ["iron_ore", "iron_ingot", "iron_pickaxe"])

    def test_unsafe_agent_zip_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "unsafe.zip"
            with zipfile.ZipFile(package, "w") as archive:
                archive.writestr("../escape.py", "raise Exception('escaped')")
            with self.assertRaisesRegex(ValueError, "unsafe path"):
                load_agent(package, root / "agent")
            self.assertFalse((root / "escape.py").exists())

    def test_invalid_action_uses_noop(self):
        from evaluation_adapters.minecraft_runner import checked_action

        self.assertEqual(checked_action({"type": "action", "value": [99] + [0] * 7}, FakeMinecraft({})), ([0] * 8, False))
        self.assertEqual(checked_action({"type": "action", "value": [1] + [0] * 7}, FakeMinecraft({})), ([1] + [0] * 7, True))

    def test_socket_message_limit(self):
        left, right = socket.socketpair()
        try:
            with self.assertRaisesRegex(ValueError, "too large"):
                JsonChannel(left).send({"payload": "x" * (2 * 1024 * 1024)})
        finally:
            left.close()
            right.close()

    def test_worker_isolates_package_from_controller_and_metrics_from_agent(self):
        import evaluation_worker

        with tempfile.TemporaryDirectory() as directory:
            scenarios = Path(directory) / "scenes.json"
            scenarios.write_text("[]", encoding="utf-8")
            job = {
                "id": "test-minecraft", "asset_url": "/asset", "lease_token": "worker-lease", "api_token": "agent-api-token",
                "config": {"adapter": "minecraft-agent-v1", "task": "open-world",
                           "resources": {"cpus": 2, "memory_mb": 4096, "time_seconds": 90, "episodes": 1},
                           "api": {"enabled": False, "max_calls": 0}, "metrics": ["task_success"]},
            }
            image = "registry.example/trusted@sha256:" + "a" * 64
            agent_image = "registry.example/agent@sha256:" + "b" * 64
            commands = []

            def mount_src(command, destination):
                mount = next(command[index + 1] for index, value in enumerate(command[:-1])
                             if value == "--mount" and f"dst={destination}" in command[index + 1])
                return Path(mount.split("src=", 1)[1].split(",dst=", 1)[0])

            def start_controller(command, **kwargs):
                commands.append(command)
                (mount_src(command, "/ipc") / "agent.sock").touch()
                return SimpleNamespace(poll=lambda: None, wait=lambda timeout: 0, returncode=0)

            def run_docker(command, **kwargs):
                commands.append(command)
                if command[:2] == ["docker", "run"]:
                    output = mount_src(commands[0], "/output")
                    (output / "result.json").write_text('{"episodes": [{"task_success": 100}]}', encoding="utf-8")
                return SimpleNamespace(returncode=0)

            with patch.object(evaluation_worker, "download") as download, \
                    patch.object(evaluation_worker.subprocess, "Popen", side_effect=start_controller), \
                    patch.object(evaluation_worker.subprocess, "run", side_effect=run_docker), \
                    patch.object(evaluation_worker, "heartbeat"):
                def write_package(_base, _path, _lease, destination):
                    destination.write_bytes(b"test package")

                download.side_effect = write_package
                result = evaluation_worker.execute("http://localhost:5000/api", job, {"minecraft-agent-v1": image},
                                                   "", "", "", agent_image, str(scenarios))
            self.assertEqual(result["episodes"], [{"task_success": 100}])
            self.assertNotIn("/input/package.zip", " ".join(commands[0]))
            self.assertNotIn("/output", " ".join(commands[1]))
            self.assertIn("--network=none", commands[1])
            self.assertIn("--read-only", commands[1])
            self.assertNotIn("--read-only", commands[0])
            self.assertNotIn("worker-lease", " ".join(commands[1]))

    def test_open_world_rejects_per_episode_api_counter(self):
        from platform_api.evaluation import validate_evaluation_config

        config = {
            "adapter": "minecraft-agent-v1", "task": "open-world",
            "resources": {"cpus": 2, "memory_mb": 4096, "gpu": False, "time_seconds": 600, "episodes": 3},
            "api": {"enabled": True, "max_calls": 30}, "metrics": ["task_success", "api_calls"],
        }
        with self.assertRaisesRegex(ValueError, "per run"):
            validate_evaluation_config(config)


if __name__ == "__main__":
    unittest.main()
