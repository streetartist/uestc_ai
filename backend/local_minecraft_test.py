"""Run the Minecraft agent protocol locally without Docker or MineDojo.

This verifies package loading, scene resets, actions, metrics and difficulty
metadata. It is a deterministic protocol smoke test, not a gameplay benchmark.
"""

from __future__ import annotations

import argparse
import json
import socket
import tempfile
import threading
import zipfile
from pathlib import Path

import numpy as np

from evaluation_adapters.minecraft_agent import load_agent, run as run_agent
from evaluation_adapters.minecraft_protocol import JsonChannel
from evaluation_adapters.minecraft_runner import evaluate


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCENARIOS = ROOT / "backend" / "data" / "minecraft-scenarios.json"
EXAMPLE_AGENT = ROOT / "backend" / "evaluation_adapters" / "minecraft" / "example_agent.py"


class SmokeActionSpace:
    nvec = [3, 3, 4, 25, 25, 8, 244, 36]

    def no_op(self):
        return [0] * 8

    def contains(self, action):
        return len(action) == 8 and all(0 <= value < limit for value, limit in zip(action, self.nvec))


class SmokeMinecraft:
    action_space = SmokeActionSpace()

    def __init__(self, scene: dict):
        self.scene = scene
        self.step_count = 0

    def observe(self):
        items = self.scene["goals"][:self.step_count]
        return {
            "rgb": np.zeros((3, 32, 32), dtype=np.uint8),
            "inventory": {"name": items, "quantity": [1] * len(items)},
            "location_stats": {"pos": [self.step_count, 64, 0]},
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
        pass


def package_example(destination: Path) -> Path:
    with zipfile.ZipFile(destination, "w") as archive:
        archive.write(EXAMPLE_AGENT, "agent.py")
        archive.write(EXAMPLE_AGENT.with_name("example_config.json"), "config.json")
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test a Minecraft agent package locally")
    parser.add_argument("--package", type=Path, help="Submission ZIP; defaults to the bundled example agent")
    parser.add_argument("--scenarios", type=Path, default=DEFAULT_SCENARIOS)
    args = parser.parse_args()

    scenarios = json.loads(args.scenarios.read_text(encoding="utf-8"))
    smoke_scenarios = [{**scene, "max_steps": len(scene["goals"]) + 2} for scene in scenarios]
    config = {
        "adapter": "minecraft-agent-v1", "task": "open-world",
        "resources": {"episodes": len(smoke_scenarios)},
        "metrics": ["task_success", "exploration_progress", "objectives", "distance_blocks", "invalid_actions"],
    }

    with tempfile.TemporaryDirectory(prefix="minecraft-smoke-") as directory:
        root = Path(directory)
        package = args.package or package_example(root / "example-agent.zip")
        agent = load_agent(package, root / "agent")
        result_path = root / "result.json"
        errors = []
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            address = probe.getsockname()

        def controller():
            try:
                evaluate(config, smoke_scenarios, SmokeMinecraft, address, result_path)
            except Exception as error:
                errors.append(error)

        thread = threading.Thread(target=controller, daemon=True)
        thread.start()
        with socket.socket() as connection:
            for _ in range(100):
                try:
                    connection.connect(address)
                    break
                except ConnectionRefusedError:
                    threading.Event().wait(0.02)
            else:
                raise RuntimeError("local evaluation controller did not start")
            run_agent(agent, JsonChannel(connection))
        thread.join(timeout=10)
        if thread.is_alive():
            raise RuntimeError("local evaluation controller did not stop")
        if errors:
            raise errors[0]
        result = json.loads(result_path.read_text(encoding="utf-8"))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if all(item["metrics"]["task_success"] == 100 for item in result["episodes"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
