import io
import json
import sys
import unittest
import os
import zipfile
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_backend_api as fixtures
import test_evaluation_limits as limits
from platform_api.extensions import db
from platform_api.ai_gateway import encrypt_keys
from platform_api.ai_models import AIChannel, AIGrant, AIProblemQuota
from platform_api.models import Problem, ProblemRuntime, Submission, User
from platform_api.compute_models import ComputeProvider, ComputePolicy
from evaluation_worker import execute
import evaluation_worker


class ProblemSetupTests(unittest.TestCase):
    setUp = fixtures.PlatformApiTestCase.setUp
    tearDown = fixtures.PlatformApiTestCase.tearDown
    login = fixtures.PlatformApiTestCase.login
    register = fixtures.PlatformApiTestCase.register
    fixture = limits.EvaluationLimitTests.fixture
    submit = limits.EvaluationLimitTests.submit

    def prepare(self):
        self.fixture(adapter="classification-v1")
        self.url = f"/api/manage/problems/{self.problem['id']}/setup"
        self.runtime_config = {"image": "sha256:" + "a" * 64, "agent_image": "", "scenarios": [{"secret_case": "private-marker"}]}
        self.ai_config = {"enabled": True, "allowed_models": ["test-model"], "max_tokens": 1000, "max_calls": 10}
        with self.app.app_context():
            self.app.config["AI_GATEWAY_ENCRYPTION_KEY"] = "setup-test"
            channel = AIChannel(name="Test Model", base_url="https://example.org", secrets=encrypt_keys(["NEVER-EXPORT-API-SECRET"]), models={"test-model": "remote-model"})
            provider = ComputeProvider(name="Test GPU", secret=encrypt_keys(["NEVER-EXPORT-AUTODL-SECRET"]), image_uuid="img", gpu_spec_uuid="gpu", gpu_label="4090", hourly_price_millis=3000)
            db.session.add_all([channel, provider]); db.session.commit()
            self.ai_config["allowed_channels"] = [channel.id]
            self.compute_config = {"provider_id": provider.id, "enabled": True, "max_gpu_seconds": 3600, "max_cost_millis": 3000}

    def document(self):
        return {"evaluation_config": deepcopy(self.config), "runtime": deepcopy(self.runtime_config), "ai": deepcopy(self.ai_config), "compute": deepcopy(self.compute_config)}

    def test_robot_runtime_private_snapshot_and_missing_environment_guard(self):
        from build_robot_arm_package import default_scenes
        self.prepare()
        self.app.config["EVALUATION_ENABLED_ADAPTERS"] = "robot-arm-agent-v1"
        self.config.update(adapter="robot-arm-agent-v1", task="multi-step", metrics=["task_success", "stable_seconds"])
        self.config["resources"]["episodes"] = 3
        response = self.admin.patch(f"/api/manage/problems/{self.problem['id']}", json={"evaluation_config": self.config})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.submit().status_code, 409)
        self.assertEqual(self.member.get(f"/api/problems/{self.problem['id']}/evaluation-budget?team_id={self.team['id']}").get_json()["used_runs"], 0)
        self.runtime_config.update(agent_image="sha256:"+"b"*64, scenarios=default_scenes())
        bad = self.document(); bad["runtime"]["scenarios"][0]["task"] = []
        self.assertEqual(self.admin.put(self.url, json=bad).status_code, 400)
        self.assertEqual(self.admin.put(self.url, json=self.document()).status_code, 200)
        submission = self.submit()
        self.assertEqual(submission.status_code, 201, submission.get_json())
        job = self.worker.post("/api/evaluation-worker/claim", headers={"Authorization": "Bearer test-worker"},
            json={"adapters": ["robot-arm-agent-v1"], "gpu": False, "managed_runtime": True}).get_json()
        self.assertEqual(job["runtime"], self.runtime_config)
        public = self.member.get(f"/api/evaluation-runs/{job['id']}").get_data(as_text=True)
        self.assertNotIn('"seed"', public)
        self.assertNotIn(self.runtime_config["image"], public)
        self.assertNotIn(self.runtime_config["agent_image"], public)

    def package(self, document, extra=None):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("problem.json", json.dumps(document))
            for name, value in (extra or {}).items(): archive.writestr(name, value)
        return output.getvalue()

    def upload(self, path, raw, **fields):
        return self.admin.post(path, data={"file": (io.BytesIO(raw), "problem.zip"), **fields}, content_type="multipart/form-data")

    def test_atomic_save_rolls_back_all_sections_and_preserves_usage(self):
        self.prepare()
        data = self.document(); data["compute"]["provider_id"] = "missing"
        response = self.admin.put(self.url, json=data)
        self.assertEqual(response.status_code, 400, response.get_json())
        with self.app.app_context():
            self.assertIsNone(db.session.get(AIProblemQuota, self.problem["id"]))
            self.assertIsNone(db.session.get(ProblemRuntime, self.problem["id"]))
        saved = self.admin.put(self.url, json=self.document())
        self.assertEqual(saved.status_code, 200, saved.get_json())
        with self.app.app_context():
            grant = AIGrant.query.filter_by(problem_id=self.problem["id"]).one()
            grant.tokens_used = 900; db.session.commit()
        bad = self.document(); bad["ai"]["max_tokens"] = 899; bad["evaluation_config"]["resources"]["time_seconds"] = 120
        self.assertEqual(self.admin.put(self.url, json=bad).status_code, 409)
        stored = self.admin.get(self.url).get_json()["config"]
        self.assertEqual(stored["evaluation_config"]["resources"]["time_seconds"], 60)
        self.assertEqual(stored["ai"]["max_tokens"], 1000)
        with self.app.app_context(): self.assertEqual(AIGrant.query.filter_by(problem_id=self.problem["id"]).one().tokens_used, 900)

    def test_private_snapshots_and_compatible_worker_routing(self):
        self.prepare()
        self.assertEqual(self.admin.put(self.url, json=self.document()).status_code, 200)
        submitted = self.submit(); self.assertEqual(submitted.status_code, 201, submitted.get_json())
        headers = {"Authorization": "Bearer test-worker"}
        worker = self.app.test_client()
        self.assertEqual(worker.get("/api/evaluation-worker/runtime-catalog").status_code, 401)
        self.assertEqual(worker.get("/api/evaluation-worker/runtime-catalog", headers=headers).get_json()["adapters"], ["classification-v1"])
        self.assertEqual(worker.post("/api/evaluation-worker/claim", headers=headers, json={"adapters": ["classification-v1"], "gpu": False}).status_code, 204)
        job = worker.post("/api/evaluation-worker/claim", headers=headers, json={"adapters": ["classification-v1"], "gpu": False, "managed_runtime": True, "legacy_adapters": [], "worker_id": "setup-worker"}).get_json()
        self.assertEqual(job["runtime"], self.runtime_config)
        self.assertTrue(next(c for c in self.admin.get(self.url).get_json()["checks"] if c["key"] == "worker")["state"] == "ready")
        for response in (self.member.get(f"/api/problems/{self.problem['id']}"), self.member.get(f"/api/evaluation-runs/{job['id']}"), self.member.get(f"/api/submissions/{submitted.get_json()['submission_id']}")):
            self.assertNotIn("private-marker", response.get_data(as_text=True))
            self.assertNotIn(self.runtime_config["image"], response.get_data(as_text=True))
        changed = self.document(); changed["runtime"]["scenarios"] = [{"changed": True}]
        self.assertEqual(self.admin.put(self.url, json=changed).status_code, 409)
        budget = self.document(); budget["evaluation_config"]["resources"]["time_seconds"] = 120
        self.assertEqual(self.admin.put(self.url, json=budget).status_code, 200)
        self.assertEqual(job["config"]["resources"]["time_seconds"], 60)

    def test_readiness_reports_required_api_disabled_or_zero_quota(self):
        self.prepare()
        data = self.document(); data["evaluation_config"]["api"] = {"enabled": True, "max_calls": 5}
        data["ai"]["enabled"] = False
        saved = self.admin.put(self.url, json=data)
        self.assertEqual(saved.status_code, 200, saved.get_json())
        self.assertEqual(next(c for c in saved.get_json()["checks"] if c["key"] == "api")["state"], "blocked")
        data["ai"].update(enabled=True, max_tokens=0)
        saved = self.admin.put(self.url, json=data)
        self.assertEqual(next(c for c in saved.get_json()["checks"] if c["key"] == "api")["state"], "blocked")

    def test_export_import_roundtrip_and_no_cloud_effects(self):
        self.prepare()
        self.assertEqual(self.admin.put(self.url, json=self.document()).status_code, 200)
        with patch("platform_api.routes.compute.AutoDL", side_effect=AssertionError("must not call")):
            export = self.admin.get(f"/api/manage/problems/{self.problem['id']}/package")
            self.assertEqual(export.status_code, 200)
            self.assertEqual(export.headers["Cache-Control"], "no-store")
            with zipfile.ZipFile(io.BytesIO(export.data)) as archive:
                manifest = json.loads(archive.read("problem.json"))
                manifest["problem"].update(code="IMPORTED-1", slug="imported-one", status="published")
                files = {name: archive.read(name) for name in archive.namelist() if name != "problem.json"}
                self.assertNotIn("NEVER-EXPORT", str(files) + str(manifest))
                self.assertNotIn(self.compute_config["provider_id"], str(manifest))
            package = self.package(manifest, files)
            preview = self.upload("/api/manage/problem-packages/preview", package)
            self.assertEqual(preview.status_code, 200, preview.get_json())
            self.assertEqual(preview.get_json()["problem"]["status"], "draft")
            self.assertEqual(preview.get_json()["scene_count"], 1)
            with self.app.app_context(): self.assertFalse(Problem.query.filter_by(slug="imported-one").first())
            imported = self.upload(f"/api/manage/tracks/{self.problem['track_id']}/problem-packages", package)
            self.assertEqual(imported.status_code, 201, imported.get_json())
            result = self.admin.get(f"/api/manage/problems/{imported.get_json()['id']}/setup").get_json()["config"]
            self.assertEqual(result["runtime"], self.runtime_config)
            self.assertEqual(result["compute"]["provider_id"], self.compute_config["provider_id"])
            self.assertEqual(result["ai"]["allowed_channels"], self.ai_config["allowed_channels"])
            self.assertEqual(self.upload(f"/api/manage/tracks/{self.problem['track_id']}/problem-packages", package).status_code, 409)

    def test_invalid_packages_and_permissions(self):
        self.prepare()
        document = {"version": 1, "problem": {"code": "NEW", "slug": "new", "title": "新题"}}
        for extra in ({"../secret": "bad"}, {"agent.py": "untrusted code"}, {"problem.json": "duplicate"}):
            self.assertEqual(self.upload("/api/manage/problem-packages/preview", self.package(document, extra)).status_code, 400)
        self.assertEqual(self.member.get(self.url).status_code, 403)
        self.assertEqual(self.member.get(f"/api/manage/problems/{self.problem['id']}/package").status_code, 403)
        invalid = self.document(); invalid["runtime"]["image"] = "python:latest"
        self.assertEqual(self.admin.put(self.url, json=invalid).status_code, 400)
        with self.app.app_context():
            user = User.query.filter_by(email="limits@std.uestc.edu.cn").one(); user.role = "organizer"; db.session.commit()
        self.assertEqual(self.member.put(self.url, json=self.document()).status_code, 403)
        self.assertEqual(self.admin.put(self.url, json=self.document()).status_code, 200)
        self.assertEqual(self.member.put(self.url, json=self.document()).status_code, 200)

    def test_minecraft_scene_validation_and_private_worker_delivery(self):
        self.prepare()
        self.app.config["EVALUATION_ENABLED_ADAPTERS"] = "minecraft-agent-v1"
        self.config.update(adapter="minecraft-agent-v1", task="open-world", metrics=["task_success"])
        self.runtime_config.update(agent_image="sha256:" + "b" * 64, scenarios=[{"id": "private-1", "label": "私有场景", "difficulty": "beginner", "task_id": "open-ended", "world_seed": 42, "max_steps": 100, "goals": ["log"]}])
        self.assertEqual(self.admin.put(self.url, json=self.document()).status_code, 200)
        bad = self.document(); bad["evaluation_config"]["resources"]["episodes"] = 2
        self.assertEqual(self.admin.put(self.url, json=bad).status_code, 400)
        self.assertEqual(self.admin.patch(f"/api/manage/problems/{self.problem['id']}", json={"evaluation_config": bad["evaluation_config"]}).status_code, 400)
        bad = self.document(); bad["runtime"]["scenarios"][0]["goals"] = [{"invalid": "object"}]
        self.assertEqual(self.admin.put(self.url, json=bad).status_code, 400)
        bad = self.document(); bad["evaluation_config"]["metrics"] = ["api_cost"]
        self.assertEqual(self.admin.put(self.url, json=bad).status_code, 400)
        job = {"config": self.config, "runtime": self.runtime_config}
        def controller(base, received, image, agent, path, *args):
            self.assertEqual(image, self.runtime_config["image"])
            self.assertEqual(agent, self.runtime_config["agent_image"])
            self.assertEqual(json.loads(Path(path).read_text()), self.runtime_config["scenarios"])
            self.private_path = path
            return {"status": "completed"}
        with patch("evaluation_worker.execute_minecraft", side_effect=controller):
            self.assertEqual(execute("base", job, {}, "", "", "")["status"], "completed")
        self.assertFalse(Path(self.private_path).exists())


class WorkerBootstrapTests(unittest.TestCase):
    def environment(self):
        return patch.dict(os.environ, {"EVALUATION_API_BASE": "http://127.0.0.1:5000/api",
            "EVALUATION_WORKER_TOKEN": "test-token", "EVALUATION_IMAGES_JSON": "{}",
            "EVALUATION_API_PROXY_URL": "", "EVALUATION_NETWORK": "", "EVALUATION_GPU_DEVICE": "", "EVALUATION_WORKER_ADAPTERS": ""})

    def test_worker_adapter_filter_does_not_claim_unrelated_queued_jobs(self):
        with self.environment(), patch.dict(os.environ, {"EVALUATION_WORKER_ADAPTERS": "robot-arm-agent-v1"}), patch.object(sys, "argv", ["worker", "--once"]), patch("evaluation_worker.subprocess.run"), patch("evaluation_worker.request_json", side_effect=[{"adapters": ["classification-v1", "robot-arm-agent-v1"]}, None]) as api:
            evaluation_worker.main()
            self.assertEqual(api.call_args_list[1].args[3]["adapters"], ["robot-arm-agent-v1"])

    def test_discovers_managed_adapters_without_environment_image_map(self):
        with self.environment(), patch.dict(os.environ, {"EVALUATION_IMAGES_JSON": '{"classification-v1":"python:latest"}'}), patch.object(sys, "argv", ["worker", "--once"]), patch("evaluation_worker.subprocess.run"), patch("evaluation_worker.request_json", side_effect=[{"adapters": ["classification-v1"]}, None]) as api:
            evaluation_worker.main()
            claim = api.call_args_list[1].args[3]
            self.assertTrue(claim["managed_runtime"])
            self.assertEqual(claim["adapters"], ["classification-v1"])
            self.assertEqual(claim["legacy_adapters"], [])
            self.assertFalse(claim["api_proxy"])

    def test_docker_failure_does_not_advertise_worker_as_online(self):
        with self.environment(), patch.object(sys, "argv", ["worker", "--once"]), patch("evaluation_worker.subprocess.run", side_effect=OSError), patch("evaluation_worker.request_json") as api:
            with self.assertRaisesRegex(SystemExit, "unavailable"):
                evaluation_worker.main()
            api.assert_not_called()

    def test_connection_failure_recovers_with_bounded_wait(self):
        class Finished(Exception): pass
        with self.environment(), patch.object(sys, "argv", ["worker"]), patch("evaluation_worker.subprocess.run"), patch("evaluation_worker.request_json", side_effect=[RuntimeError("connection lost"), {"adapters": ["classification-v1"]}, None]) as api, patch("evaluation_worker.threading.Event") as event, patch("evaluation_worker.print"):
            event.return_value.wait.side_effect = [None, Finished()]
            with self.assertRaises(Finished): evaluation_worker.main()
            self.assertEqual(api.call_args_list[-1].args[1], "/evaluation-worker/claim")
            self.assertEqual([call.args[0] for call in event.return_value.wait.call_args_list], [2, 5])


if __name__ == "__main__": unittest.main()
