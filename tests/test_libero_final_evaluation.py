import io
import json
import sys
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_backend_api as fixtures
import test_evaluation_limits as limits
from platform_api.extensions import db
from platform_api.evaluation import adapters
from platform_api.evaluation_trials import package_digest
from platform_api.models import EvaluationRun, Problem, ProblemRuntime, Submission, SubmissionVersion, User
from evaluation_adapters.wujie_scenes import libero_scenes


class LiberoFinalTests(unittest.TestCase):
    setUp = fixtures.PlatformApiTestCase.setUp
    tearDown = fixtures.PlatformApiTestCase.tearDown
    register = fixtures.PlatformApiTestCase.register
    login = fixtures.PlatformApiTestCase.login
    base_fixture = limits.EvaluationLimitTests.fixture

    def fixture(self):
        self.base_fixture()
        self.config.update(adapter="libero-agent-v1", task="manipulation")
        self.app.config["EVALUATION_ENABLED_ADAPTERS"] = "libero-agent-v1"
        self.runtime_config = {"image": "sha256:" + "a" * 64, "agent_image": "sha256:" + "b" * 64,
                               "scenarios": libero_scenes()[:1]}
        with self.app.app_context():
            problem = db.session.get(Problem, self.problem["id"])
            problem.evaluation_config = deepcopy(self.config)
            problem.scoring_config = {"final_evaluation_required": True}
            problem.track.competition.ends_at = datetime.now(timezone.utc) + timedelta(days=1)
            db.session.add(ProblemRuntime(problem=problem, config=deepcopy(self.runtime_config)))
            user_id = User.query.filter_by(email="limits@std.uestc.edu.cn").one().id
            submission = Submission(problem=problem, team_id=self.team["id"], title="Test", status="draft")
            version = SubmissionVersion(submission=submission, version=1, status="draft", snapshot={}, fields={}, readme_md="",
                created_by=user_id)
            db.session.add(version)
            db.session.flush()
            path = Path(self.app.config["UPLOAD_FOLDER"]) / "development.zip"
            path.write_bytes(b"tested package")
            trial = EvaluationRun(problem=problem, submission_version=version, purpose="trial", status="completed",
                runtime_snapshot=deepcopy(self.runtime_config), package_storage_name=path.name,
                config_snapshot={**deepcopy(self.config), "metric_definitions": [adapters()["libero-agent-v1"]["metrics"][0]]},
                submission_snapshot={"package_sha256": package_digest(path)},
                episodes=[{"metrics": {"task_success": 100}}], metrics={"task_success": 100})
            db.session.add(trial)
            db.session.commit()
            self.trial_id = trial.id
        asset = self.stage()
        submitted = self.member.post("/api/submissions", json={**self.fields,
            "staged_asset_ids": [asset], "evaluation_run_id": self.trial_id})
        self.assertEqual(submitted.status_code, 201, submitted.get_json())
        self.version_id = submitted.get_json()["version"]["id"]
        self.submission_id = submitted.get_json()["id"]
        self.url = f"/api/submission-versions/{self.version_id}/final-evaluation-runs"
        self.final_runtime = deepcopy(self.runtime_config)
        self.final_runtime["scenarios"][0]["init_state_id"] = 40
        self.request_body = {"scene_set_version": "candidate-v1", "runtime": self.final_runtime,
                             "evaluation_config": self.config}

    def stage(self):
        response = self.member.post("/api/submission-assets/stage", data={
            "file": (io.BytesIO(b"tested package"), "agent.zip")}, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 201)
        return response.get_json()["id"]

    def close(self):
        with self.app.app_context():
            problem = db.session.get(Problem, self.problem["id"])
            problem.track.competition.ends_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            db.session.commit()

    def claim(self):
        response = self.worker.post("/api/evaluation-worker/claim", headers={"Authorization": "Bearer test-worker"},
            json={"adapters": ["libero-agent-v1"], "gpu": False, "managed_runtime": True})
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def test_final_result_replaces_only_formal_score_and_is_private_until_published(self):
        self.fixture()
        work_url = f"/api/works/{self.submission_id}"
        work = self.member.get(work_url).get_json()
        self.assertIsNone(work["evaluation"])
        self.assertEqual(work["development_evaluation"]["id"], self.trial_id)
        self.assertTrue(work["final_evaluation_required"])
        self.assertEqual(self.member.post(self.url, json=self.request_body).status_code, 403)
        self.assertEqual(self.admin.post(self.url, json=self.request_body).status_code, 409)
        self.close()
        started = self.admin.post(self.url, json=self.request_body)
        self.assertEqual(started.status_code, 201, started.get_json())
        run = started.get_json()
        self.assertEqual(run["purpose"], "final")
        self.assertNotIn("runtime", run)
        self.assertEqual(self.member.get(f"/api/evaluation-runs/{run['id']}").status_code, 403)
        history = self.member.get(f"/api/problems/{self.problem['id']}/evaluation-runs?team_id={self.team['id']}").get_json()
        self.assertEqual([item["id"] for item in history], [self.trial_id])
        self.assertEqual(self.member.get(f"/api/evaluation-runs/{run['id']}/logs").status_code, 404)
        self.assertEqual(self.admin.post(self.url, json=self.request_body).status_code, 409)
        self.assertEqual(self.member.post("/api/submissions", json={**self.fields, "status": "draft"}).status_code, 409)
        job = self.claim()
        self.assertEqual(job["runtime"]["scenarios"][0]["init_state_id"], 40)
        self.assertEqual(job["submission"]["package_sha256"], run["package_sha256"])
        self.assertEqual(self.member.post(f"/api/evaluation-worker/runs/{run['id']}/complete", json={"status": "completed"}).status_code, 403)
        completed = self.worker.post(f"/api/evaluation-worker/runs/{run['id']}/complete",
            headers={"X-Evaluation-Lease": job["lease_token"]},
            json={"status": "completed", "episodes": [{"scenario": {key: self.final_runtime["scenarios"][0][key]
                for key in ("id", "label", "difficulty")}, "metrics": {"task_success": 0}}]})
        self.assertEqual(completed.status_code, 200, completed.get_json())
        self.assertIsNone(self.member.get(work_url).get_json()["evaluation"])
        self.assertEqual(self.admin.post(f"/api/evaluation-runs/{run['id']}/publish").status_code, 200)
        work = self.member.get(work_url).get_json()
        self.assertEqual(work["evaluation"]["metrics"]["task_success"], 0)
        self.assertEqual(work["development_evaluation"]["metrics"]["task_success"], 100)
        self.assertEqual(self.member.get(self.budget_url).get_json()["used_runs"], 0)

    def test_five_task_final_freezes_weights_and_counts_the_public_tasks(self):
        self.fixture()
        metrics = ["completion_score", "efficiency_score", "automatic_score"]
        self.config["metrics"] = metrics
        self.config["resources"]["episodes"] = 5
        public = libero_scenes()
        scenes = [{**s, "scoring": {"version": 2, "reference_steps": [100] * count}}
                  for s, count in zip(public, (1, 1, 2))]
        scenes += [{**public[2], "id": "hidden-" + str(i), "init_state_id": 40 + i,
                    "scoring": {"version": 2, "reference_steps": [100, 200]}} for i in range(2)]
        with self.app.app_context():
            problem = db.session.get(Problem, self.problem["id"])
            problem.evaluation_config = deepcopy(self.config)
            problem.scoring_config = {"final_evaluation_required": True,
                "performance_scoring": {"preset": "wujie-libero-v2", "criterion": "表现"}}
            db.session.commit()
        body = {**self.request_body, "evaluation_config": self.config,
                "runtime": {**self.final_runtime, "scenarios": scenes}}
        self.close()
        started = self.admin.post(self.url, json=body)
        self.assertEqual(started.status_code, 201, started.get_json())
        run = started.get_json()
        job = self.claim()
        self.assertEqual(len(job["runtime"]["scenarios"]), 5)
        episodes = [{"scenario": {key: scene[key] for key in ("id", "label", "difficulty")},
                     "metrics": {"completion_score": 60 if i < 3 else 0,
                                 "efficiency_score": 40 if i < 3 else 0,
                                 "automatic_score": 100 if i < 3 else 0}} for i, scene in enumerate(scenes)]
        completed = self.worker.post(f"/api/evaluation-worker/runs/{run['id']}/complete",
            headers={"X-Evaluation-Lease": job["lease_token"]},
            json={"status": "completed", "episodes": episodes})
        self.assertEqual(completed.status_code, 200, completed.get_json())
        result = self.admin.get(f"/api/evaluation-runs/{run['id']}").get_json()
        self.assertEqual(result["performance"]["score"], 50)

    def test_interrupted_trial_persists_completed_scenes_without_a_score(self):
        self.fixture()
        scenes = libero_scenes()
        with self.app.app_context():
            run = db.session.get(EvaluationRun, self.trial_id)
            run.status = "queued"
            run.episodes = []
            run.metrics = {}
            config = deepcopy(run.config_snapshot)
            config["resources"]["episodes"] = 3
            run.config_snapshot = config
            run.runtime_snapshot = {**self.runtime_config, "scenarios": scenes}
            db.session.commit()
        job = self.claim()
        partial = {"episodes": [{"scenario": {key: scenes[0][key] for key in ("id", "label", "difficulty")},
                                 "metrics": {"task_success": 100}}],
                   "planned_episodes": 3, "interrupted_scene": {key: scenes[1][key] for key in ("id", "label", "difficulty")},
                   "reason": "evaluation_time_limit"}
        url = f"/api/evaluation-worker/runs/{self.trial_id}/complete"
        payload = {"status": "failed", "error": "evaluation time limit exceeded", "partial_results": partial}
        self.assertEqual(self.member.post(url, json=payload).status_code, 403)
        for change in ({"planned_episodes": 2}, {"reason": "student_failure"},
                       {"interrupted_scene": partial["episodes"][0]["scenario"]},
                       {"episodes": [{**partial["episodes"][0], "metrics": {"task_success": 200}}]},
                       {"episodes": [{**partial["episodes"][0], "scenario": partial["interrupted_scene"]}]}):
            with self.subTest(change=change):
                response = self.worker.post(url, headers={"X-Evaluation-Lease": job["lease_token"]},
                    json={**payload, "partial_results": {**partial, **change}})
                self.assertEqual(response.status_code, 400, response.get_json())
        response = self.worker.post(url, headers={"X-Evaluation-Lease": job["lease_token"]}, json=payload)
        self.assertEqual(response.status_code, 200, response.get_json())
        # Read again through the student endpoint: this exercises persistence and access.
        result = self.member.get(f"/api/evaluation-runs/{self.trial_id}").get_json()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["partial_results"], partial)
        self.assertEqual(result["episodes"], partial["episodes"])
        self.assertEqual(result["metrics"], {})
        self.assertIsNone(result["performance"])

    def test_final_partial_results_remain_private_and_cannot_be_published(self):
        self.fixture()
        self.close()
        run = self.admin.post(self.url, json=self.request_body).get_json()
        job = self.claim()
        partial = {"episodes": [], "planned_episodes": 1,
                   "interrupted_scene": {key: self.final_runtime["scenarios"][0][key] for key in ("id", "label", "difficulty")},
                   "reason": "execution_error"}
        url = f"/api/evaluation-worker/runs/{run['id']}/complete"
        invalid = self.worker.post(url, headers={"X-Evaluation-Lease": job["lease_token"]},
            json={"status": "completed", "partial_results": partial})
        self.assertEqual(invalid.status_code, 400)
        response = self.worker.post(url, headers={"X-Evaluation-Lease": job["lease_token"]},
            json={"status": "failed", "partial_results": partial})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self.member.get(f"/api/evaluation-runs/{run['id']}").status_code, 403)
        self.assertEqual(self.admin.get(f"/api/evaluation-runs/{run['id']}").get_json()["partial_results"], partial)
        self.assertEqual(self.admin.post(f"/api/evaluation-runs/{run['id']}/publish").status_code, 409)

    def test_scene_reuse_seed_only_changes_and_budget_changes_are_rejected(self):
        self.fixture()
        self.close()
        duplicate = deepcopy(self.request_body)
        duplicate["runtime"] = deepcopy(self.runtime_config)
        duplicate["runtime"]["scenarios"][0].update(id="renamed", seed=987, max_steps=700)
        self.assertEqual(self.admin.post(self.url, json=duplicate).status_code, 400)
        started = self.admin.post(self.url, json=self.request_body).get_json()
        job = self.claim()
        failed = self.worker.post(f"/api/evaluation-worker/runs/{started['id']}/complete",
            headers={"X-Evaluation-Lease": job["lease_token"]}, json={"status": "failed", "error": "infrastructure failure"})
        self.assertEqual(failed.status_code, 200)
        changed = deepcopy(self.request_body)
        changed["evaluation_config"]["resources"]["time_seconds"] += 30
        self.assertEqual(self.admin.post(self.url, json=changed).status_code, 409)
        retried = self.admin.post(self.url, json=self.request_body)
        self.assertEqual(retried.status_code, 201, retried.get_json())
        self.assertEqual(self.member.get(self.budget_url).get_json()["used_runs"], 0)

    def test_frozen_package_bytes_are_verified_at_worker_download(self):
        self.fixture()
        self.close()
        self.assertEqual(self.admin.post(self.url, json=self.request_body).status_code, 201)
        job = self.claim()
        with self.app.app_context():
            version = db.session.get(SubmissionVersion, self.version_id)
            (Path(self.app.config["UPLOAD_FOLDER"]) / version.assets[0].storage_name).write_bytes(b"changed")
        response = self.worker.get(job["asset_url"], headers={"X-Evaluation-Lease": job["lease_token"]})
        self.assertEqual(response.status_code, 409)

    def test_final_api_requires_organizer_channel_and_invalid_result_is_not_accepted(self):
        self.fixture()
        self.close()
        body = deepcopy(self.request_body)
        body["evaluation_config"]["api"] = {"enabled": True, "max_calls": 10}
        self.app.config["EVALUATION_FINAL_API_POLICY"] = {"allowed_models": ["deepseek-flash"],
            "max_output_tokens": 32768, "reasoning_efforts": ["low"]}
        self.assertEqual(self.admin.post(self.url, json=body).status_code, 503)
        self.assertEqual(self.member.get(self.budget_url).get_json()["used_runs"], 0)
        self.assertEqual(self.admin.post(self.url, json=self.request_body).status_code, 201)
        job = self.claim()
        response = self.worker.post(f"/api/evaluation-worker/runs/{job['id']}/complete",
            headers={"X-Evaluation-Lease": job["lease_token"]},
            json={"status": "completed", "episodes": [{"task_success": 100}]})
        self.assertEqual(response.status_code, 400)

    def test_final_proxy_keeps_visual_thinking_and_uses_frozen_bounded_policy(self):
        self.fixture()
        self.close()
        self.app.config.update(EVALUATION_API_URL="https://example.invalid/v1/chat/completions",
            EVALUATION_API_KEY="test-only-not-a-key", EVALUATION_FINAL_API_POLICY={
                "allowed_models": ["deepseek-flash"], "max_output_tokens": 32768, "reasoning_efforts": ["low"]})
        body = deepcopy(self.request_body)
        body["evaluation_config"]["api"] = {"enabled": True, "max_calls": 10}
        started = self.admin.post(self.url, json=body)
        self.assertEqual(started.status_code, 201, started.get_json())
        job = self.claim()
        payload = {"model": "deepseek-flash", "max_tokens": 32768, "thinking": {"type": "enabled"},
            "reasoning_effort": "low", "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + "a" * 12000}} for _ in range(6)]}]}
        self.assertGreater(len(json.dumps(payload).encode()), 65536)
        headers = {"X-Evaluation-API-Token": job["api_token"]}
        url = f"/api/evaluation-worker/runs/{job['id']}/api"
        upstream = MagicMock()
        upstream.open.return_value.__enter__.return_value.status = 200
        upstream.open.return_value.__enter__.return_value.read.return_value = b'{"choices": []}'
        with patch("platform_api.routes.evaluations.urllib.request.build_opener", return_value=upstream):
            result = self.worker.post(url, headers=headers, json=payload)
        self.assertEqual(result.status_code, 200, result.get_json())
        self.assertEqual(upstream.open.call_args.kwargs["timeout"], 120)
        forwarded = json.loads(upstream.open.call_args.args[0].data)
        self.assertEqual(forwarded["thinking"], {"type": "enabled"})
        self.assertEqual(forwarded["reasoning_effort"], "low")
        self.assertEqual(forwarded["max_tokens"], 32768)
        self.assertEqual(self.worker.post(url, headers=headers, json={**payload, "max_tokens": 32769}).status_code, 400)
        self.assertEqual(self.worker.post(url, headers=headers, json={**payload, "model": "other-model"}).status_code, 403)
        self.assertEqual(self.worker.post(url, headers=headers, json={**payload, "reasoning_effort": "max"}).status_code, 400)
        self.assertEqual(self.worker.post(url, headers=headers, json={**payload, "thinking": {"type": "enabled", "budget_tokens": 999999}}).status_code, 400)
        self.assertEqual(self.admin.get(f"/api/evaluation-runs/{job['id']}").get_json()["api_calls_used"], 1)
        self.assertEqual(self.member.get(self.budget_url).get_json()["used_runs"], 0)

        from platform_api.models import AuditLog
        with self.app.app_context():
            record = AuditLog.query.filter_by(action="evaluation.final_api_call", entity_id=job['id']).one()
            self.assertEqual(record.details['outcome'], 'completed')
            self.assertEqual(record.details['upstream_status'], 200)
            self.assertGreaterEqual(record.details['elapsed_ms'], 0)
            self.assertNotIn('test-only-not-a-key', json.dumps(record.details))
        import urllib.error
        upstream.open.side_effect = urllib.error.HTTPError('https://example.invalid', 503, 'unavailable', {}, None)
        with patch("platform_api.routes.evaluations.urllib.request.build_opener", return_value=upstream):
            failed = self.worker.post(url, headers=headers, json=payload)
        self.assertEqual(failed.status_code, 502)
        with self.app.app_context():
            records = AuditLog.query.filter_by(action="evaluation.final_api_call", entity_id=job['id']).all()
            self.assertEqual(len(records), 2)
            self.assertTrue(any(r.details['upstream_status'] == 503 and r.details['outcome'] == 'upstream_http_error' for r in records))


if __name__ == "__main__":
    unittest.main()
