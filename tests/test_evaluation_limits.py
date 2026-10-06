import io
import sys
import threading
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_backend_api as fixtures
from platform_api.extensions import db
from platform_api.models import EvaluationRun, Submission
from platform_api.evaluation import validate_evaluation_config


class EvaluationLimitTests(unittest.TestCase):
    setUp = fixtures.PlatformApiTestCase.setUp
    tearDown = fixtures.PlatformApiTestCase.tearDown
    login = fixtures.PlatformApiTestCase.login
    register = fixtures.PlatformApiTestCase.register

    def fixture(self, adapter="minecraft-agent-v1", limit=2):
        self.app.config.update(EVALUATION_WORKER_TOKEN="test-worker", EVALUATION_ENABLED_ADAPTERS=adapter)
        self.login(self.admin, "admin@uestcai.top")
        self.register(self.member, "limits@std.uestc.edu.cn", "Limits")
        competition = self.member.get("/api/competitions/paper-city-2027").get_json()
        track = next(item for item in competition["tracks"] if item["slug"] == "story-maps")
        self.problem = track["problems"][0]
        self.config = {
            "adapter": adapter, "task": "survival" if adapter == "minecraft-agent-v1" else "classification",
            "resources": {"cpus": 2, "memory_mb": 2048, "gpu": False, "time_seconds": 60, "episodes": 1},
            "api": {"enabled": False, "max_calls": 0},
            "metrics": ["task_success"] if adapter == "minecraft-agent-v1" else ["accuracy"],
        }
        if limit is not None:
            self.config["max_team_runs"] = limit
        result = self.admin.patch(f"/api/manage/problems/{self.problem['id']}", json={"evaluation_config": self.config})
        self.assertEqual(result.status_code, 200, result.get_json())
        self.team = self.member.post("/api/teams", json={"competition_id": competition["id"], "name": "Limits"}).get_json()
        self.member.post("/api/registrations", json={"competition_id": competition["id"], "track_id": track["id"], "team_id": self.team["id"]})
        self.fields = {"problem_id": self.problem["id"], "team_id": self.team["id"], "title": "Test", "readme_md": "# Test", "status": "submitted"}
        self.budget_url = f"/api/problems/{self.problem['id']}/evaluation-budget?team_id={self.team['id']}"
        self.worker = self.app.test_client()

    def submit(self, client=None, **changes):
        client = client or self.member
        staged = client.post("/api/submission-assets/stage", data={"file": (io.BytesIO(b"test"), "agent.zip")}, content_type="multipart/form-data")
        self.assertEqual(staged.status_code, 201)
        data = {**self.fields, "staged_asset_ids": [staged.get_json()["id"]], **changes}
        if data["status"] == "draft":
            return client.post("/api/submissions", json=data)
        return client.post(f"/api/problems/{data['problem_id']}/evaluation-runs", json=data)

    def claim(self):
        response = self.worker.post("/api/evaluation-worker/claim", headers={"Authorization": "Bearer test-worker"}, json={"adapters": [self.config["adapter"]], "gpu": False})
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def test_uniform_limit_survives_overwrites_failures_and_drafts(self):
        self.fixture()
        self.assertEqual(self.member.get(self.budget_url).get_json()["remaining_runs"], 2)
        self.assertEqual(self.submit(status="draft").status_code, 201)
        self.assertEqual(self.member.get(self.budget_url).get_json()["used_runs"], 0)
        first = self.submit()
        self.assertEqual(first.status_code, 201, first.get_json())
        job = self.claim()
        failed = self.worker.post(f"/api/evaluation-worker/runs/{job['id']}/complete", headers={"X-Evaluation-Lease": job["lease_token"]}, json={"status": "failed", "error": "evaluation time limit exceeded"})
        self.assertEqual(failed.status_code, 200)
        self.assertEqual(self.submit().status_code, 201)
        blocked = self.submit(title="Must not overwrite")
        self.assertEqual(blocked.status_code, 429, blocked.get_json())
        stored = self.member.get(f"/api/submissions/{first.get_json()['submission_id']}").get_json()
        self.assertNotEqual(stored["title"], "Must not overwrite")
        self.assertEqual(self.member.get(self.budget_url).get_json()["remaining_runs"], 0)
        self.assertEqual(self.submit(status="draft").status_code, 201)
        self.assertEqual(self.member.delete(f"/api/submissions/{first.get_json()['submission_id']}").status_code, 409)
        self.assertEqual(self.submit().status_code, 429)

    def test_budget_changes_keep_usage_and_queued_job_snapshot(self):
        self.fixture(limit=1)
        self.assertEqual(self.submit().status_code, 201)
        updated = deepcopy(self.config)
        updated["max_team_runs"] = 2
        updated["resources"]["time_seconds"] = 120
        changed = self.admin.patch(f"/api/manage/problems/{self.problem['id']}", json={"evaluation_config": updated})
        self.assertEqual(changed.status_code, 200, changed.get_json())
        job = self.claim()
        self.assertEqual(job["config"]["resources"]["time_seconds"], 60)
        self.assertEqual(self.member.get(self.budget_url).get_json()["used_runs"], 1)
        self.worker.post(f"/api/evaluation-worker/runs/{job['id']}/complete", headers={"X-Evaluation-Lease": job["lease_token"]}, json={"status": "failed"})
        self.assertEqual(self.submit().status_code, 201)
        self.assertEqual(self.claim()["config"]["resources"]["time_seconds"], 120)
        updated["max_team_runs"] = 1
        self.assertEqual(self.admin.patch(f"/api/manage/problems/{self.problem['id']}", json={"evaluation_config": updated}).status_code, 200)
        self.assertEqual(self.member.get(self.budget_url).get_json()["remaining_runs"], 0)
        updated["metrics"] = ["deaths"]
        self.assertEqual(self.admin.patch(f"/api/manage/problems/{self.problem['id']}", json={"evaluation_config": updated}).status_code, 409)

    def test_invalid_submission_and_worker_reclaim_do_not_charge_extra(self):
        self.fixture(limit=1)
        self.assertEqual(self.member.post("/api/submissions", json=self.fields).status_code, 400)
        self.assertEqual(self.member.get(self.budget_url).get_json()["used_runs"], 0)
        self.app.config["EVALUATION_WORKER_TOKEN"] = ""
        self.assertEqual(self.submit().status_code, 503)
        self.assertEqual(self.member.get(self.budget_url).get_json()["used_runs"], 0)
        self.app.config["EVALUATION_WORKER_TOKEN"] = "test-worker"
        self.assertEqual(self.submit().status_code, 201)
        job = self.claim()
        with self.app.app_context():
            run = db.session.get(EvaluationRun, job["id"])
            run.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            db.session.commit()
        reclaimed = self.claim()
        self.assertEqual(reclaimed["id"], job["id"])
        self.assertEqual(self.member.get(self.budget_url).get_json()["used_runs"], 1)
        self.assertEqual(self.submit().status_code, 429)

    def test_team_scope_and_generic_adapter(self):
        self.fixture(adapter="classification-v1", limit=1)
        self.assertEqual(self.submit().status_code, 201)
        other = self.app.test_client()
        self.register(other, "other-limits@std.uestc.edu.cn", "Other")
        self.assertEqual(other.get(self.budget_url).status_code, 403)
        self.assertEqual(self.app.test_client().get(self.budget_url).status_code, 401)
        second_team = other.post("/api/teams", json={"competition_id": self.team["competition_id"], "name": "Other"}).get_json()
        competition = self.member.get("/api/competitions/paper-city-2027").get_json()
        track = next(item for item in competition["tracks"] if item["slug"] == "story-maps")
        other.post("/api/registrations", json={"competition_id": competition["id"], "track_id": track["id"], "team_id": second_team["id"]})
        self.assertEqual(self.submit(client=other, team_id=second_team["id"]).status_code, 201)
        self.assertEqual(self.submit(client=other, team_id=second_team["id"]).status_code, 429)
        second_problem = track["problems"][1]
        self.assertEqual(self.admin.patch(f"/api/manage/problems/{second_problem['id']}", json={"evaluation_config": self.config}).status_code, 200)
        self.assertEqual(self.submit(problem_id=second_problem["id"]).status_code, 201)

    def test_concurrent_submissions_cannot_exceed_limit(self):
        self.fixture(limit=1)
        cookie = self.member.get_cookie("session_token")
        barrier = threading.Barrier(2)
        responses = []
        errors = []
        def attempt():
            try:
                client = self.app.test_client()
                client.set_cookie("session_token", cookie.value)
                staged = client.post("/api/submission-assets/stage", data={"file": (io.BytesIO(b"test"), "agent.zip")}, content_type="multipart/form-data").get_json()
                barrier.wait(timeout=10)
                responses.append(client.post(f"/api/problems/{self.problem['id']}/evaluation-runs", json={**self.fields, "staged_asset_ids": [staged["id"]]}).status_code)
            except Exception as error:
                errors.append(str(error))
        threads = [threading.Thread(target=attempt) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)
        self.assertEqual(errors, [])
        self.assertEqual(sorted(responses), [201, 429])
        with self.app.app_context():
            self.assertEqual(EvaluationRun.query.count(), 1)
            self.assertEqual(Submission.query.first().evaluation_runs_used, 1)

    def test_optional_legacy_limit_and_strict_validation(self):
        self.fixture(limit=None)
        self.assertEqual(self.submit().status_code, 201)
        self.assertEqual(self.submit().status_code, 409)
        job = self.claim()
        self.worker.post(f"/api/evaluation-worker/runs/{job['id']}/complete", headers={"X-Evaluation-Lease": job["lease_token"]}, json={"status": "failed"})
        self.assertEqual(self.submit().status_code, 201)
        self.assertIsNone(self.member.get(self.budget_url).get_json()["remaining_runs"])
        for value in (0, -1, 1001, True, 1.5, "3", None):
            with self.subTest(value=value), self.app.app_context(), self.assertRaises(ValueError):
                validate_evaluation_config({**self.config, "max_team_runs": value})


if __name__ == "__main__":
    unittest.main()
