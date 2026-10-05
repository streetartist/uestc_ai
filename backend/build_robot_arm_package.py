"""Build a portable draft problem ZIP from locally built immutable images."""
from __future__ import annotations
import argparse
import json
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def default_scenes():
    return [{"id": f"panda-{task}", "label": label, "difficulty": difficulty, "task": task,
             "seed": seed, "max_steps": 600, "hold_steps": 10,
             **({"target": [.12, .10]} if task == "place" else {})}
            for task, label, difficulty, seed in (("lift", "抓取并抬升", "beginner", 42),
                ("place", "指定位置放置", "intermediate", 43), ("stack", "积木堆叠", "challenge", 44))]


def build_package(controller, agent, scenes=None, config=None):
    template = json.loads((ROOT / "backend/platform_api/templates/robot-arm-manipulation.json").read_text(encoding="utf-8"))["problem"]
    scenes = scenes if scenes is not None else default_scenes()
    config = config or template["evaluation_config"]
    return {"version": 1, "problem": template, "setup": {"evaluation_config": config}}, {
        "runtime.json": {"image": controller, "agent_image": agent}, "scenarios.json": scenes}


def write_package(path, controller, agent, scenes=None, config=None):
    manifest, files = build_package(controller, agent, scenes, config)
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("problem.json", json.dumps(manifest, ensure_ascii=False))
        for name, value in files.items():
            archive.writestr(name, json.dumps(value, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controller-image", default="local/uestc-robot-arm-controller:dev")
    parser.add_argument("--agent-image", default="local/uestc-robot-arm-agent:dev")
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/robot-arm-problem.zip")
    args = parser.parse_args()
    images = [subprocess.check_output(["docker", "image", "inspect", "--format", "{{.Id}}", name], text=True).strip()
              for name in (args.controller_image, args.agent_image)]
    write_package(args.output, *images)
    print(args.output.resolve())


if __name__ == "__main__":
    main()
