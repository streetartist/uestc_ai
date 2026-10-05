"""Real Panda robot-arm acceptance: submit ZIP -> Docker worker -> reviewer metrics.

Uses an isolated SQLite database and short PUBLIC acceptance scenes. It does not
connect a model provider or enable the adapter in the development/production DB.
Build the two Robot arm images first; run this with Docker available on PATH.
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
from build_robot_arm_package import default_scenes
from platform_api import create_app
from platform_api.extensions import db


ROOT = Path(__file__).resolve().parents[1]
METRICS = ["task_success", "stable_grasps", "recovery_success", "action_count", "execution_seconds", "invalid_actions", "stable_seconds"]
TEST_AGENT = (ROOT / "backend/evaluation_adapters/robot_arm/example_agent.py").read_text(encoding="utf-8")



def checked(response, code=200):
    if response.status_code != code:
        raise RuntimeError(f"HTTP {response.status_code}: {response.get_json()}")
    return response.get_json()


def image_id(name: str):
    return subprocess.check_output(["docker", "image", "inspect", "--format", "{{.Id}}", name], text=True).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controller-image", default="local/uestc-robot-arm-controller:dev")
    parser.add_argument("--agent-image", default="local/uestc-robot-arm-agent:dev")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--package", type=Path, help="Optional contestant ZIP instead of the acceptance agent")
    parser.add_argument("--time-limit", type=int, default=900, help="Total runtime budget across all scenes, in seconds")
    parser.add_argument("--expect-timeout", action="store_true", help="Run a deliberately slow agent and verify timeout, cleanup and quota")
    parser.add_argument("--negative", action="store_true", help="Verify no-op, invalid actions and forged success cannot pass")
    parser.add_argument("--vision", action="store_true", help="Run the public RGB baseline on randomized layouts")
    args = parser.parse_args()
    if not 30 <= args.time_limit <= 14400:
        parser.error("--time-limit must be between 30 and 14400")
    if args.expect_timeout and args.package:
        parser.error("--expect-timeout uses the built-in slow agent")
    if args.negative and (args.package or args.expect_timeout):
        parser.error("--negative uses the built-in invalid / no-op agent")
    if args.vision and (args.package or args.negative or args.expect_timeout):
        parser.error("--vision uses the public RGB baseline")
    output = (args.output or ROOT / "outputs" / ("robot-arm-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S"))).resolve()
    output.mkdir(parents=True, exist_ok=False)
    controller, agent = image_id(args.controller_image), image_id(args.agent_image)
    scenes = default_scenes()
    for scene in scenes:
        if not args.vision:
            scene["placements"] = {"red": [-.05, -.05], "green": [.08, .08]}
        scene["max_steps"] = 700
        if args.negative:
            scene["max_steps"] = 30
    scene_path = output / "scenarios.json"
    scene_path.write_text(json.dumps(scenes, ensure_ascii=False), encoding="utf-8")
    package = args.package
    if package is None:
        package = output / "agent.zip"
        with zipfile.ZipFile(package, "w") as archive:
            source = TEST_AGENT.replace("red = [-.05, -.05]", "import time\n        time.sleep(86400)\n        red = [-.05, -.05]") if args.expect_timeout else TEST_AGENT
            if args.vision:
                source = (ROOT / "backend/evaluation_adapters/robot_arm/vision_agent.py").read_text(encoding="utf-8")
            if args.negative:
                source = '''class Agent:
    def reset(self, goal): self.goal = goal
    def act(self, observation):
        import os
        assert not os.path.exists('/scenarios/scenarios.json')
        assert not os.path.exists('/output/result.json')
        assert 'cubeA_pos' not in observation and 'seed' not in observation
        assert observation['images_jpeg_base64']['agentview']
        if self.goal['task'] == 'lift': return {'delta': [999] * 7}
        if self.goal['task'] == 'stack': return {'task_success': 100}
        return {'delta': [0,0,0,0,0,0,-1], 'repeat': 10}
'''
            archive.writestr("agent.py", source)
            archive.writestr("config.json", "{}")
    config = {"adapter": "robot-arm-agent-v1", "task": "multi-step",
              "resources": {"cpus": 2, "memory_mb": 4096, "gpu": False, "time_seconds": args.time_limit, "episodes": 3},
              "api": {"enabled": False, "max_calls": 0}, "metrics": METRICS, "max_team_runs": 1}
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///" + (output / "test.sqlite3").as_posix(),
                      "UPLOAD_FOLDER": str(output / "uploads"), "AUTO_CREATE_SCHEMA": True, "SEED_DATABASE": True,
                      "ENFORCE_COMPETITION_DEADLINES": False, "INITIAL_ADMIN_PASSWORD": "Acceptance123!",
                      "INITIAL_REVIEWER_PASSWORD": "Acceptance123!", "EXPOSE_VERIFICATION_CODE": True,
                      "EVALUATION_WORKER_TOKEN": secrets.token_urlsafe(32),
                      "EVALUATION_ENABLED_ADAPTERS": "robot-arm-agent-v1"})
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    admin, member, reviewer, worker = (app.test_client() for _ in range(4))
    try:
        print(f"Artifacts: {output}", flush=True)
        checked(admin.post("/api/auth/login", json={"email": "admin@uestcai.top", "password": "Acceptance123!"}))
        competition = checked(member.get("/api/competitions/paper-city-2027"))
        track = competition["tracks"][0]
        template = json.loads((ROOT / "backend/platform_api/templates/robot-arm-manipulation.json").read_text(encoding="utf-8"))["problem"]
        bundle = io.BytesIO()
        with zipfile.ZipFile(bundle, "w") as archive:
            archive.writestr("problem.json", json.dumps({"version": 1, "problem": template,
                "setup": {"evaluation_config": config}}, ensure_ascii=False))
            archive.writestr("runtime.json", json.dumps({"image": controller, "agent_image": agent}))
            archive.writestr("scenarios.json", json.dumps(scenes, ensure_ascii=False))
        (output / "problem-package.zip").write_bytes(bundle.getvalue())
        checked(admin.post("/api/manage/problem-packages/preview", data={"file": (io.BytesIO(bundle.getvalue()), "robot.zip")}, content_type="multipart/form-data"))
        problem = checked(admin.post(f"/api/manage/tracks/{track['id']}/problem-packages", data={"file": (io.BytesIO(bundle.getvalue()), "robot.zip")}, content_type="multipart/form-data"), 201)
        assert problem["status"] == "draft"
        checked(admin.patch(f"/api/manage/problems/{problem['id']}", json={"status": "published"}))
        email = "robot-acceptance@std.uestc.edu.cn"
        challenge = checked(member.post("/api/auth/captcha", json={"email": email}), 200)
        code = checked(member.post("/api/auth/verification-codes", json={"email": email, "captcha_id": challenge["id"], "captcha_code": challenge["debug_code"]}), 202)["debug_code"]
        checked(member.post("/api/auth/register", json={"email": email, "name": "机械臂验收", "password": "Acceptance123!", "confirm_password": "Acceptance123!", "verification_code": code}), 201)
        team = checked(member.post("/api/teams", json={"competition_id": competition["id"], "name": "机械臂 Docker 验收"}), 201)
        checked(member.post("/api/registrations", json={"competition_id": competition["id"], "track_id": track["id"], "team_id": team["id"]}), 201)
        assets = []
        for filename, contents in [("agent.zip", package.read_bytes()), ("report.pdf", b"%PDF-1.4\n% acceptance placeholder, not a research report\n%%EOF")]:
            assets.append(checked(member.post("/api/submission-assets/stage", data={"file": (io.BytesIO(contents), filename)}, content_type="multipart/form-data"), 201)["id"])
        submission = checked(member.post("/api/submissions", json={"problem_id": problem["id"], "team_id": team["id"], "title": "真实 Panda 验收",
                             "readme_md": "# Docker 验收\n公开短场景，仅验证评测链路。", "fields": {"runtime_notes": "Acceptance agent"},
                             "status": "submitted", "staged_asset_ids": assets}), 201)
        job = checked(worker.post("/api/evaluation-worker/claim", headers={"Authorization": "Bearer " + app.config["EVALUATION_WORKER_TOKEN"]}, json={"adapters": ["robot-arm-agent-v1"], "gpu": False, "managed_runtime": True}))
        assert job["runtime"]["scenarios"] == scenes
        assert "placements" not in json.dumps(checked(member.get(f"/api/evaluation-runs/{job['id']}")))
        print("Submitted and claimed; running real robosuite / MuJoCo controller + isolated ZIP agent", flush=True)
        started = time.monotonic()
        try:
            result = execute(f"http://127.0.0.1:{server.server_port}/api", job,
                {}, "", "", "", log_directory=str(output))
        except subprocess.TimeoutExpired:
            failed = checked(worker.post(f"/api/evaluation-worker/runs/{job['id']}/complete", headers={"X-Evaluation-Lease": job["lease_token"]}, json={"status": "failed", "error": "evaluation time limit exceeded"}))
            if not args.expect_timeout:
                raise
            budget = checked(member.get(f"/api/problems/{problem['id']}/evaluation-budget?team_id={team['id']}"))
            assert budget["used_runs"] == 1 and budget["remaining_runs"] == 0
            checked(member.post("/api/submissions", json={"problem_id": problem["id"], "team_id": team["id"], "title": "Blocked retest", "status": "submitted"}), 429)
            remaining = subprocess.check_output(["docker", "ps", "-aq", "--filter", "label=uestc.evaluation_run=" + job["id"]], text=True).strip()
            assert not remaining, "Timed-out Robot arm containers were not removed"
            report = {"status": "passed", "environment": "real robosuite / MuJoCo / Panda", "time_limit_seconds": args.time_limit,
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
        if args.package is None and not args.negative and not args.vision:
            episodes = completed["episodes"]
            assert [item["metrics"]["task_success"] for item in episodes] == [100, 100, 100], episodes
            assert all(item["metrics"]["stable_seconds"] >= .5 for item in episodes)
            assert all(item["metrics"]["stable_grasps"] > 0 for item in episodes)
            assert all(item["metrics"]["invalid_actions"] == 0 for item in episodes)
        if args.negative:
            assert [item["metrics"]["task_success"] for item in completed["episodes"]] == [0, 0, 0]
            assert [item["metrics"]["invalid_actions"] for item in completed["episodes"]] == [30, 0, 30]
        if args.vision:
            assert any(item["metrics"]["task_success"] > 0 for item in completed["episodes"]), "RGB baseline did not solve any task"
        report = {"status": "passed", "environment": "real robosuite / MuJoCo / Panda", "controller_image": controller,
                  "agent_image": agent, "reviewer_metrics_verified": True,
                  "imported_problem_runtime": True, "evaluation": completed}
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
