"""Run a participant Agent with the official local environment, without using server trials."""
from __future__ import annotations

import argparse
import json
import socket
import threading
import time
from pathlib import Path

from evaluation_adapters.minecraft_agent import load_agent, run
from evaluation_adapters.minecraft_protocol import JsonChannel


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--scenes", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    scenes = json.loads(args.scenes.read_text(encoding="utf-8"))
    if scenes and "suite" in scenes[0]:
        from evaluation_adapters.libero_runner import evaluate, create_environment
        adapter, task = "libero-agent-v1", "manipulation"
        metrics = ["task_success", "action_count", "execution_seconds", "invalid_actions"]
        factory = create_environment
    else:
        from evaluation_adapters.minecraft_runner import evaluate, create_minedojo_environment
        adapter, task = "minecraft-agent-v1", "open-world"
        metrics = ["task_success", "exploration_progress", "objectives", "unique_items", "distance_blocks",
                   "tech_milestones", "deaths", "invalid_actions", "mean_step_ms"]
        factory = create_minedojo_environment
    agent = load_agent(args.package, args.output / "agent")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        address = probe.getsockname()
    errors = []

    def controller():
        try:
            evaluate({"adapter": adapter, "task": task, "metrics": metrics,
                      "resources": {"episodes": len(scenes), "time_seconds": 3600}}, scenes,
                     factory, address, args.output / "result.json")
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=controller, daemon=True)
    thread.start()
    with socket.socket() as connection:
        for _ in range(600):
            try:
                connection.connect(address)
                break
            except ConnectionRefusedError:
                if errors:
                    raise errors[0]
                time.sleep(.2)
        else:
            raise TimeoutError("practice controller did not start")
        connection.settimeout(3600)
        run(agent, JsonChannel(connection))
    thread.join(timeout=10)
    if errors:
        raise errors[0]
    if thread.is_alive():
        raise TimeoutError("practice controller did not finish")
    print((args.output / "result.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
