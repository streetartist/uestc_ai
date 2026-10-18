"""Generate the C-only website import and activation configuration."""
import argparse
import json
from pathlib import Path
import zipfile

from build_wujie_competition import build_catalogue
from evaluation_adapters.wujie_scenes import libero_scored_scenes
from platform_api.evaluation import validate_evaluation_config
from platform_api.problem_setup import read_package, validate_runtime

ROOT = Path(__file__).resolve().parents[1]
AGENT_IMAGE = "sha256:923f54c25f6c7f15b59b35bbc062e452320c54dde8e599321ff55be70eb901a8"


def build(output, agent_image=AGENT_IMAGE):
    manifest = json.loads((ROOT / "public/downloads/libero-release.json").read_text(encoding="utf-8"))
    track = next(t for t in build_catalogue()["tracks"] if t["slug"] == "embodied-agent")
    problem = track["problem"]
    config = validate_evaluation_config(problem["evaluation_config"])
    runtime = validate_runtime({"image": manifest["practice"]["image"], "agent_image": agent_image,
                                "scenarios": libero_scored_scenes()}, config)
    document = {key: problem[key] for key in ("statement_md", "summary", "compute_note",
                "evaluation_config", "scoring_config", "judging_schema")}
    document["runtime"] = runtime
    output.mkdir(parents=True, exist_ok=True)
    (output / "site-config.json").write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    package = output / "wujie-robot-arm-agent.zip"
    with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("problem.json", json.dumps({"version": 1, "problem": problem,
                          "setup": {"runtime": runtime, "evaluation_config": config}}, ensure_ascii=False))
    read_package(package.read_bytes())
    return document


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/libero-release")
    parser.add_argument("--agent-image", default=AGENT_IMAGE)
    args = parser.parse_args()
    build(args.output, args.agent_image)
    print(args.output.resolve())
