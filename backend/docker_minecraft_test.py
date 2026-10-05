"""Real Minecraft acceptance: submit ZIP -> Docker worker -> reviewer metrics.

Uses an isolated SQLite database and short PUBLIC acceptance scenes. It does not
connect a model provider or enable the adapter in the development/production DB.
Build the two Minecraft images first; run this with Docker available on PATH.
"""
from __future__ import annotations

import argparse
import io
import json
import secrets
import subprocess
import threading
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from werkzeug.serving import make_server

from evaluation_worker import execute
from platform_api import create_app
from platform_api.extensions import db


ROOT = Path(__file__).resolve().parents[1]
METRICS = ["task_success", "exploration_progress", "objectives", "unique_items", "distance_blocks",
           "tech_milestones", "deaths", "invalid_actions", "mean_step_ms"]
TEST_AGENT = '''class Agent:
    def reset(self, goal):
        self.goal = goal
    def act(self, observation):
        import os
        assert not os.path.exists('/output/result.json')
        assert not os.path.exists('/scenarios/scenarios.json')
        assert observation['image_jpeg_base64']
        if self.goal == ['diamond']:
            return [999] * 8
        action = list(observation['noop_action'])
        action[0] = 1
        return action
'''


def checked(response, code=200):
    if response.status_code != code:
        raise RuntimeError(f"HTTP {response.status_code}: {response.get_json()}")
    return response.get_json()


def image_id(name: str):
    return subprocess.check_output(["docker", "image", "inspect", "--format", "{{.Id}}", name], text=True).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controller-image", default="local/uestc-minecraft-controller:dev")
    parser.add_argument("--agent-image", default="local/uestc-minecraft-agent:dev")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--package", type=Path, help="Optional contestant ZIP instead of the acceptance agent")
    parser.add_argument("--time-limit", type=int, default=900, help="Total runtime budget across all scenes, in seconds")
    parser.add_argument("--expect-timeout", action="store_true", help="Run a deliberately slow agent and verify timeout, cleanup and quota")
    args = parser.parse_args()
    if not 30 <= args.time_limit <= 14400:
        parser.error("--time-limit must be between 30 and 14400")
    if args.expect_timeout and args.package:
        parser.error("--expect-timeout uses the built-in slow agent")
    output = (args.output or ROOT / "outputs" / ("minecraft-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S"))).resolve()
    output.mkdir(parents=True, exist_ok=False)
    controller, agent = image_id(args.controller_image), image_id(args.agent_image)
    scenes = []
    for index, (label, difficulty, goals, inventory) in enumerate([
        ("验收：已满足目标", "beginner", ["stick"], [{"slot": 0, "name": "stick", "quantity": 1}]),
        ("验收：部分完成与移动", "intermediate", ["stick", "diamond"], [{"slot": 0, "name": "stick", "quantity": 1}]),
        ("验收：未完成与无效动作", "challenge", ["diamond"], []),
    ]):
        scenes.append({"id": f"acceptance-{index + 1}", "label": label, "difficulty": difficulty,
                       "task_id": "open-ended", "world_seed": 1042 + index, "max_steps": 8,
                       "goals": goals, "initial_inventory": inventory, "generate_world_type": "flat",
                       "start_time": 1000, "allow_mob_spawn": False, "allow_time_passage": False})
    scene_path = output / "scenarios.json"
    scene_path.write_text(json.dumps(scenes, ensure_ascii=False), encoding="utf-8")
    package = args.package
    if package is None:
        package = output / "agent.zip"
        with zipfile.ZipFile(package, "w") as archive:
            source = TEST_AGENT.replace("import os", "import time\n        time.sleep(86400)\n        import os") if args.expect_timeout else TEST_AGENT
            archive.writestr("agent.py", source)
            archive.writestr("config.json", "{}")
    config = {"adapter": "minecraft-agent-v1", "task": "open-world",
              "resources": {"cpus": 4, "memory_mb": 6144, "gpu": False, "time_seconds": args.time_limit, "episodes": 3},
              "api": {"enabled": False, "max_calls": 0}, "metrics": METRICS, "max_team_runs": 1}
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///" + (output / "test.sqlite3").as_posix(),
                      "UPLOAD_FOLDER": str(output / "uploads"), "AUTO_CREATE_SCHEMA": True, "SEED_DATABASE": True,
                      "ENFORCE_COMPETITION_DEADLINES": False, "INITIAL_ADMIN_PASSWORD": "Acceptance123!",
                      "INITIAL_REVIEWER_PASSWORD": "Acceptance123!", "EXPOSE_VERIFICATION_CODE": True,
                      "EVALUATION_WORKER_TOKEN": secrets.token_urlsafe(32),
                      "EVALUATION_ENABLED_ADAPTERS": "minecraft-agent-v1"})
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    admin, member, reviewer, worker = (app.test_client() for _ in range(4))
    try:
        print(f"Artifacts: {output}", flush=True)
        checked(admin.post("/api/auth/login", json={"email": "admin@uestcai.top", "password": "Acceptance123!"}))
        competition = checked(member.get("/api/competitions/paper-city-2027"))
        track = competition["tracks"][0]
        template = json.loads((ROOT / "backend/platform_api/templates/minecraft-open-world.json").read_text(encoding="utf-8"))["problem"]
        problem = checked(admin.post(f"/api/tracks/{track['id']}/problems", json={**template, "status": "published", "evaluation_config": config}), 201)
        email = "mc-acceptance@std.uestc.edu.cn"
        code = checked(member.post("/api/auth/verification-codes", json={"email": email}), 202)["debug_code"]
        checked(member.post("/api/auth/register", json={"email": email, "name": "MC 验收", "password": "Acceptance123!", "verification_code": code}), 201)
        team = checked(member.post("/api/teams", json={"competition_id": competition["id"], "name": "MC Docker 验收"}), 201)
        checked(member.post("/api/registrations", json={"competition_id": competition["id"], "track_id": track["id"], "team_id": team["id"]}), 201)
        assets = []
        for filename, contents in [("agent.zip", package.read_bytes()), ("report.pdf", b"%PDF-1.4\n% acceptance placeholder, not a research report\n%%EOF")]:
            assets.append(checked(member.post("/api/submission-assets/stage", data={"file": (io.BytesIO(contents), filename)}, content_type="multipart/form-data"), 201)["id"])
        submission = checked(member.post("/api/submissions", json={"problem_id": problem["id"], "team_id": team["id"], "title": "真实 Minecraft 验收",
                             "readme_md": "# Docker 验收\n公开短场景，仅验证评测链路。", "fields": {"runtime_notes": "Acceptance agent"},
                             "status": "submitted", "staged_asset_ids": assets}), 201)
        job = checked(worker.post("/api/evaluation-worker/claim", headers={"Authorization": "Bearer " + app.config["EVALUATION_WORKER_TOKEN"]}, json={"adapters": ["minecraft-agent-v1"], "gpu": False}))
        print("Submitted and claimed; running real MineDojo controller + isolated ZIP agent", flush=True)
        started = time.monotonic()
        try:
            result = execute(f"http://127.0.0.1:{server.server_port}/api", job, {"minecraft-agent-v1": controller}, "", "", "", agent, str(scene_path), str(output))
        except subprocess.TimeoutExpired:
            failed = checked(worker.post(f"/api/evaluation-worker/runs/{job['id']}/complete", headers={"X-Evaluation-Lease": job["lease_token"]}, json={"status": "failed", "error": "evaluation time limit exceeded"}))
            if not args.expect_timeout:
                raise
            budget = checked(member.get(f"/api/problems/{problem['id']}/evaluation-budget?team_id={team['id']}"))
            assert budget["used_runs"] == 1 and budget["remaining_runs"] == 0
            checked(member.post("/api/submissions", json={"problem_id": problem["id"], "team_id": team["id"], "title": "Blocked retest", "status": "submitted"}), 429)
            remaining = subprocess.check_output(["docker", "ps", "-aq", "--filter", "label=uestc.evaluation_run=" + job["id"]], text=True).strip()
            assert not remaining, "Timed-out Minecraft containers were not removed"
            report = {"status": "passed", "environment": "real MineDojo / Minecraft", "time_limit_seconds": args.time_limit,
                      "elapsed_seconds_including_cleanup": round(time.monotonic() - started, 2),
                      "timeout_verified": True, "containers_removed": True, "quota_enforced": True,
                      "evaluation": failed, "budget": budget}
            (output / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
            return
        except Exception as error:
            checked(worker.post(f"/api/evaluation-worker/runs/{job['id']}/complete", headers={"X-Evaluation-Lease": job["lease_token"]}, json={"status": "failed", "error": str(error)}))
            raise
        if args.expect_timeout:
            raise AssertionError("The deliberately slow agent was not stopped by its time limit")
        (output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        completed = checked(worker.post(f"/api/evaluation-worker/runs/{job['id']}/complete", headers={"X-Evaluation-Lease": job["lease_token"]}, json=result))
        checked(reviewer.post("/api/auth/login", json={"email": "reviewer@uestc.ai", "password": "Acceptance123!"}))
        queue = checked(reviewer.get("/api/review-queue"))
        visible = next(item["evaluation"] for item in queue if item["id"] == submission["version"]["id"])
        assert visible["status"] == "completed" and visible["metrics"] == completed["metrics"]
        if args.package is None:
            episodes = completed["episodes"]
            assert [item["metrics"]["exploration_progress"] for item in episodes] == [100, 50, 0]
            assert [item["metrics"]["task_success"] for item in episodes] == [100, 0, 0]
            assert [item["metrics"]["objectives"] for item in episodes] == [1, 1, 0]
            assert [item["metrics"]["unique_items"] for item in episodes] == [1, 1, 0]
            assert episodes[2]["metrics"]["invalid_actions"] == 8
            assert episodes[1]["metrics"]["distance_blocks"] > 0
            assert episodes[1]["metrics"]["mean_step_ms"] > 0
        report = {"status": "passed", "environment": "real MineDojo / Minecraft", "controller_image": controller,
                  "agent_image": agent, "reviewer_metrics_verified": True, "evaluation": completed}
        (output / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        with app.app_context():
            db.session.remove()
            db.engine.dispose()


if __name__ == "__main__":
    main()
