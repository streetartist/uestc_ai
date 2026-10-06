import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_backend_api as fixtures
import test_evaluation_limits as limits
from platform_api.extensions import db
from platform_api.models import EvaluationRun


class IndependentTrialTests(unittest.TestCase):
    setUp = fixtures.PlatformApiTestCase.setUp
    tearDown = fixtures.PlatformApiTestCase.tearDown
    register = fixtures.PlatformApiTestCase.register
    login = fixtures.PlatformApiTestCase.login
    fixture = limits.EvaluationLimitTests.fixture
    claim = limits.EvaluationLimitTests.claim

    def stage(self, raw=b"first tested package", client=None):
        response = (client or self.member).post("/api/submission-assets/stage", data={
            "file": (io.BytesIO(raw), "agent.zip")}, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()["id"]

    def start(self, asset_id):
        return self.member.post(f"/api/problems/{self.problem['id']}/evaluation-runs", json={
            "team_id": self.team["id"], "staged_asset_ids": [asset_id]})

    def complete(self, job, success=100):
        response = self.worker.post(f"/api/evaluation-worker/runs/{job['id']}/complete", headers={
            "X-Evaluation-Lease": job["lease_token"]}, json={"status": "completed", "episodes": [{"task_success": success}]})
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def formal(self, asset_id, run_id):
        return self.member.post("/api/submissions", json={**self.fields,
            "staged_asset_ids": [asset_id], "evaluation_run_id": run_id})

    def test_private_trials_then_shared_formal_result_without_extra_charge(self):
        self.fixture(limit=1)
        asset = self.stage()
        no_result = self.formal(asset, "missing")
        self.assertEqual(no_result.status_code, 409)
        self.assertEqual(self.member.get(self.budget_url).get_json()["used_runs"], 0)
        trial = self.start(asset)
        self.assertEqual(trial.status_code, 201, trial.get_json())
        run = trial.get_json()
        self.assertEqual(run["purpose"], "trial")
        self.assertEqual(self.app.test_client().get(f"/api/works/{run['submission_id']}").status_code, 404)
        self.login(self.reviewer, "reviewer@uestc.ai")
        self.assertEqual(self.reviewer.get(f"/api/evaluation-runs/{run['id']}").status_code, 403)
        self.assertEqual(self.formal(asset, run["id"]).status_code, 409)
        job = self.claim()
        self.complete(job, 75)
        submitted = self.formal(asset, run["id"])
        self.assertEqual(submitted.status_code, 201, submitted.get_json())
        self.assertEqual(self.member.get(self.budget_url).get_json()["used_runs"], 1)
        self.assertEqual(self.start(self.stage()).status_code, 429)
        url = f"/api/works/{run['submission_id']}"
        self.assertEqual(self.member.get(url).get_json()["evaluation"]["metrics"], {"task_success": 75})
        self.assertEqual(self.reviewer.get(url).get_json()["evaluation"]["metrics"], {"task_success": 75})
        self.assertIsNone(self.app.test_client().get(url).get_json()["evaluation"])
        self.assertEqual(self.reviewer.get(f"/api/evaluation-runs/{run['id']}").status_code, 200)
        # Other members cannot see either private history or the selected metrics.
        other = self.app.test_client()
        self.register(other, "peer@std.uestc.edu.cn", "Peer")
        history = f"/api/problems/{self.problem['id']}/evaluation-runs?team_id={self.team['id']}"
        self.assertEqual(other.get(history).status_code, 403)
        self.assertIsNone(other.get(url).get_json()["evaluation"])
        self.assertEqual(other.get(f"/api/evaluation-runs/{run['id']}").status_code, 403)
        # Re-submit the same package/result after exhausting the quota.
        resubmitted = self.formal(self.stage(), run["id"])
        self.assertEqual(resubmitted.status_code, 201, resubmitted.get_json())
        self.assertEqual(self.member.get(self.budget_url).get_json()["used_runs"], 1)
        self.assertEqual(len(self.member.get(history).get_json()), 1)

    def test_frozen_input_survives_draft_replacement_and_staged_file_removal(self):
        self.fixture()
        asset = self.stage()
        trial = self.start(asset).get_json()
        self.assertEqual(self.member.delete(f"/api/submission-assets/stage/{asset}").status_code, 204)
        replacement = self.stage(b"second package")
        draft = self.member.post("/api/submissions", json={**self.fields, "status": "draft", "staged_asset_ids": [replacement]})
        self.assertEqual(draft.status_code, 201)
        job = self.claim()
        downloaded = self.worker.get(job["asset_url"], headers={"X-Evaluation-Lease": job["lease_token"]}, buffered=True)
        self.assertEqual(downloaded.data, b"first tested package")
        downloaded.close()
        self.complete(job)
        changed = self.formal(self.stage(b"second package"), trial["id"])
        self.assertEqual(changed.status_code, 409)
        self.assertEqual(changed.get_json()["error"], "submitted package differs from selected evaluation trial")
        self.assertEqual(self.member.get(f"/api/evaluation-runs/{trial['id']}").get_json()["status"], "completed")

    def test_new_tests_do_not_replace_formal_result_and_old_success_can_be_selected(self):
        self.fixture(limit=2)
        first_asset = self.stage()
        first = self.start(first_asset).get_json()
        self.complete(self.claim(), 50)
        self.assertEqual(self.formal(first_asset, first["id"]).status_code, 201)
        second = self.start(self.stage(b"new version")).get_json()
        self.assertEqual(self.member.get(f"/api/works/{first['submission_id']}").get_json()["evaluation"]["id"], first["id"])
        job = self.claim()
        self.worker.post(f"/api/evaluation-worker/runs/{job['id']}/complete", headers={"X-Evaluation-Lease": job["lease_token"]}, json={"status": "failed", "error": "evaluation time limit exceeded"})
        self.assertEqual(self.formal(self.stage(b"new version"), second["id"]).status_code, 409)
        self.assertEqual(self.formal(self.stage(), first["id"]).status_code, 201)
        records = self.member.get(f"/api/problems/{self.problem['id']}/evaluation-runs?team_id={self.team['id']}")
        self.assertEqual(records.headers["Cache-Control"], "private, no-store")
        self.assertEqual([item["status"] for item in records.get_json()], ["failed", "completed"])

    def test_copy_failure_and_invalid_materials_never_charge_or_publish(self):
        self.fixture()
        self.assertEqual(self.start("unknown").status_code, 400)
        asset = self.stage()
        with patch("platform_api.routes.evaluation_trials.shutil.copyfile", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.start(asset)
        self.assertEqual(self.member.get(self.budget_url).get_json()["used_runs"], 0)
        with self.app.app_context():
            self.assertEqual(EvaluationRun.query.count(), 0)
        self.assertEqual(self.start(asset).status_code, 201)


if __name__ == "__main__":
    unittest.main()
