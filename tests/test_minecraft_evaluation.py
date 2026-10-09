from __future__ import annotations

import json
import hashlib
import io
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
from evaluation_adapters.minecraft.prepare_assets import prepare_asset  # noqa: E402


class MinecraftAssetTests(unittest.TestCase):
    def test_worker_download_resolves_api_asset_url_without_duplicate_prefix(self):
        from evaluation_worker import download

        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "package.zip"
            with patch("urllib.request.urlopen", return_value=io.BytesIO(b"package")) as request:
                download("http://127.0.0.1:5000/api", "/api/evaluation-worker/runs/run/asset", "lease", destination)
            self.assertEqual(request.call_args.args[0].full_url,
                             "http://127.0.0.1:5000/api/evaluation-worker/runs/run/asset")
            self.assertEqual(destination.read_bytes(), b"package")

    def test_worker_download_rejects_cross_origin_lease_disclosure(self):
        from evaluation_worker import download

        with patch("urllib.request.urlopen") as request:
            with self.assertRaisesRegex(RuntimeError, "API origin"):
                download("http://127.0.0.1:5000/api", "https://example.com/asset", "lease", Path("unused.zip"))
        request.assert_not_called()

    def test_verified_cache_does_not_require_network(self):
        data = b"minecraft asset"
        digest = hashlib.sha1(data).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "objects" / digest[:2] / digest
            target.parent.mkdir(parents=True)
            target.write_bytes(data)
            with patch("urllib.request.urlopen") as request:
                prepare_asset({"hash": digest, "size": len(data)}, root)
            request.assert_not_called()

    def test_corrupt_cache_is_replaced_from_official_https_host(self):
        data = b"minecraft asset"
        digest = hashlib.sha1(data).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "objects" / digest[:2] / digest
            target.parent.mkdir(parents=True)
            target.write_bytes(b"corrupt")
            with patch("urllib.request.urlopen", return_value=io.BytesIO(data)) as request:
                prepare_asset({"hash": digest, "size": len(data)}, root)
            self.assertTrue(request.call_args.args[0].startswith("https://resources.download.minecraft.net/"))
            self.assertEqual(target.read_bytes(), data)

    def test_asset_checksum_mismatch_fails_without_publishing_file(self):
        digest = hashlib.sha1(b"expected").hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("urllib.request.urlopen", return_value=io.BytesIO(b"wrong!!!")):
                with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                    prepare_asset({"hash": digest, "size": 8}, root)
            self.assertFalse((root / "objects" / digest[:2] / digest).exists())


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
                    "task_id": "open-ended", "world_seed": seed, "max_steps": 5,
                    "goals": goals, "objective_labels": [f"目标 {index + 1}" for index in range(len(goals))],
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
            self.assertEqual([objective["completed"] for objective in results[2]["objectives"]], [True, True, True])
            self.assertEqual(results[2]["objectives"][1], {
                "id": "iron_ingot", "label": "目标 2", "completed": True,
            })

    def test_objective_labels_must_match_scene_goals(self):
        from evaluation_adapters.minecraft_runner import validate_scenarios

        scene = {
            "id": "bad-labels", "label": "标签错误", "difficulty": "beginner",
            "task_id": "open-ended", "world_seed": 1, "max_steps": 10,
            "goals": ["log", "planks"], "objective_labels": ["获取原木"],
        }
        with self.assertRaisesRegex(ValueError, "objective labels"):
            validate_scenarios([scene], 1)

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
                           "resources": {"cpus": 2, "memory_mb": 2048, "time_seconds": 90, "episodes": 1},
                           "api": {"enabled": False, "max_calls": 0}, "metrics": ["task_success"]},
            }
            image = "registry.example/trusted@sha256:" + "a" * 64
            agent_image = "registry.example/agent@sha256:" + "b" * 64
            commands = []
            mounted_output = []
            uploaded = []

            def mount_src(command, destination):
                mount = next(command[index + 1] for index, value in enumerate(command[:-1])
                             if value == "--mount" and f"dst={destination}" in command[index + 1])
                return Path(mount.split("src=", 1)[1].split(",dst=", 1)[0])

            def start_controller(command, **kwargs):
                commands.append(command)
                output = mount_src(command, "/output")
                mounted_output.append(output)
                scene = output / "evidence" / "1"
                # The trusted container writes into host-owned directories,
                # even if a root controller would otherwise create them.
                (scene / "replay.gif").write_bytes(b"GIF89a")
                (scene / "trajectory.json").write_text('{"steps": []}', encoding="utf-8")
                if any("type=bind" in part and "dst=/ipc" in part for part in command):
                    (mount_src(command, "/ipc") / "agent.sock").touch()
                return SimpleNamespace(poll=lambda: None, wait=lambda timeout: 0, returncode=0)

            def run_docker(command, **kwargs):
                commands.append(command)
                if command[:2] == ["docker", "run"]:
                    output = mount_src(commands[0], "/output")
                    (output / "result.json").write_text('{"episodes": [{"task_success": 100}]}', encoding="utf-8")
                return SimpleNamespace(returncode=0)

            with patch.object(evaluation_worker, "download") as download, \
                    patch('evaluation_docker_limits.node_memory_budget', return_value=6144), \
                    patch.object(evaluation_worker.subprocess, "Popen", side_effect=start_controller), \
                    patch.object(evaluation_worker.subprocess, "run", side_effect=run_docker), \
                    patch.object(evaluation_worker, "heartbeat"), \
                    patch.object(evaluation_worker, "upload_evidence_file", side_effect=lambda base, job, name, path: uploaded.append((name, path.read_bytes()))) as upload:
                def write_package(_base, _path, _lease, destination):
                    destination.write_bytes(b"test package")

                download.side_effect = write_package
                result = evaluation_worker.execute("http://localhost:5000/api", job, {"minecraft-agent-v1": image},
                                                   "", "", "", agent_image, str(scenarios))
            self.assertEqual(result["episodes"], [{"task_success": 100}])
            self.assertEqual(upload.call_count, 2)
            self.assertEqual(uploaded[0], ("scene-1-replay.gif", b"GIF89a"))
            self.assertEqual(uploaded[1][0], "scene-1-trajectory.json")
            self.assertFalse(mounted_output[0].exists())
            containers = [command for command in commands if command[:2] == ["docker", "run"]]
            self.assertEqual(len(containers), 2)
            self.assertNotIn("/input/package.zip", " ".join(containers[0]))
            self.assertNotIn("/output", " ".join(containers[1]))
            self.assertIn("--network=none", containers[1])
            self.assertIn("--read-only", containers[1])
            self.assertNotIn("--read-only", containers[0])
            self.assertIn("--pids-limit=512", containers[0])
            self.assertIn("--pids-limit=256", containers[1])
            self.assertIn('--memory=4096m', containers[0])
            self.assertIn('--memory-swap=4096m', containers[0])
            self.assertIn('--memory=2048m', containers[1])
            self.assertIn('--memory-swap=2048m', containers[1])
            self.assertIn("EVALUATION_SOCKET_TIMEOUT=90", containers[1])
            self.assertNotIn("worker-lease", " ".join(containers[1]))

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
