import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_independent_evaluation_trials import IndependentTrialTests
from platform_api.ai_gateway import GatewayError, encrypt_keys
from platform_api.compute_models import ComputeProvider
from platform_api.extensions import db
from platform_api.judge import claim_pool, process_pool, refund_unstarted, available
from platform_api.judge_models import JudgePool
from platform_api.models import EvaluationRun, EvaluationWorkerState, Problem, utcnow


class FakeAPI:
    def __init__(self):
        self.status_value = "shutdown"
        self.calls = []
        self.lose_reply = False
        self.reject = False
        self.unavailable = False

    def status(self, remote):
        if self.unavailable:
            raise GatewayError("offline", 502, "provider_unavailable")
        return self.status_value

    def request(self, action, body):
        self.calls.append((action, body))
        pool = JudgePool.query.one()
        # Durable intent is visible to another DB transaction before dispatch.
        self.assert_intent = pool.state == "starting" and pool.dispatched_at is not None
        if self.reject:
            raise GatewayError("no stock", 502, "provider_rejected")
        self.status_value = "running"
        if self.lose_reply:
            self.lose_reply = False
            raise GatewayError("reply lost", 502, "provider_unavailable")

    def power_off(self, remote):
        self.calls.append(("power_off", remote))
        self.status_value = "shutdown"


class AutomaticJudgeTests(unittest.TestCase):
    setUp = IndependentTrialTests.setUp
    tearDown = IndependentTrialTests.tearDown
    login = IndependentTrialTests.login
    register = IndependentTrialTests.register
    fixture = IndependentTrialTests.fixture
    stage = IndependentTrialTests.stage
    start = IndependentTrialTests.start

    def prepare(self):
        self.fixture(adapter="classification-v1")
        self.config["resources"]["gpu"] = True
        self.app.config["AI_GATEWAY_ENCRYPTION_KEY"] = "judge-unit-secret"
        self.runtime_config = {"execution": "autodl-native", "image": "image-organizer",
            "agent_image": "", "scenarios": [{"dataset": "private-depth", "manifest_sha256": "a" * 64}]}
        response = self.admin.put(f"/api/manage/problems/{self.problem['id']}/setup",
            json={"evaluation_config": self.config, "runtime": self.runtime_config})
        self.assertEqual(response.status_code, 200, response.get_json())
        with self.app.app_context():
            provider = ComputeProvider(name="private judge", base_url="https://api.autodl.com", secret=encrypt_keys(["test-token"]),
                image_uuid="image-participant", gpu_spec_uuid="4080", gpu_label="4080", hourly_price_millis=1680)
            db.session.add(provider); db.session.commit()
            self.provider_id = provider.id
        self.settings = {"provider_id": self.provider_id, "remote_id": "pro-organizer", "worker_id": "trusted-judge",
            "enabled": True, "idle_seconds": 30, "boot_seconds": 60}
        response = self.admin.put(f"/api/manage/problems/{self.problem['id']}/judge", json=self.settings)
        self.assertEqual(response.status_code, 200, response.get_json())
        self.pool_id = response.get_json()["id"]
        self.api = FakeAPI()
        self.tick()
        self.capabilities = {"adapters": ["classification-v1"], "gpu": True, "managed_runtime": True,
            "worker_id": "trusted-judge", "api_proxy": False, "legacy_adapters": [],
            "execution_backends": ["autodl-native"], "runtime_images": ["image-organizer"],
            "dataset_manifests": {"private-depth": "a" * 64}}

    def tick(self):
        with self.app.app_context():
            pool = db.session.get(JudgePool, self.pool_id)
            pool.checked_at = utcnow() - timedelta(seconds=6)
            db.session.commit()
            identity = claim_pool("scheduler")
            self.assertEqual(identity, self.pool_id)
            process_pool(identity, "scheduler", lambda provider: self.api)

    def advertise(self):
        response = self.worker.post("/api/evaluation-worker/claim", json=self.capabilities,
            headers={"Authorization": "Bearer test-worker"})
        self.assertEqual(response.status_code, 204, response.get_json())
        self.tick()

    def claim(self, **changes):
        return self.worker.post("/api/evaluation-worker/claim", json={**self.capabilities, **changes},
            headers={"Authorization": "Bearer test-worker"})

    def test_wake_once_private_affinity_result_and_idle_shutdown(self):
        self.prepare()
        run = self.start(self.stage()).get_json()
        self.assertEqual(run["dispatch"]["state"], "off")
        self.assertEqual(self.start(self.stage()).status_code, 409)
        self.tick(); self.tick()
        self.assertEqual(len(self.api.calls), 1)
        self.assertTrue(self.api.assert_intent)
        self.assertEqual(self.api.calls[0][1]["instance_uuid"], "pro-organizer")
        self.advertise()
        self.assertEqual(self.claim(worker_id="participant").status_code, 204)
        self.assertEqual(self.claim(dataset_manifests={"private-depth": "b" * 64}).status_code, 204)
        job = self.claim().get_json()
        self.assertEqual(job["id"], run["id"])
        self.assertEqual(self.claim().status_code, 204)  # One live run per instance.
        completed = self.worker.post(f"/api/evaluation-worker/runs/{run['id']}/complete", headers={"X-Evaluation-Lease": job["lease_token"]},
            json={"status": "completed", "episodes": [{"accuracy": 50}]})
        self.assertEqual(completed.status_code, 200, completed.get_json())
        self.tick()
        with self.app.app_context():
            pool = db.session.get(JudgePool, self.pool_id)
            pool.idle_since = utcnow() - timedelta(seconds=31)
            db.session.commit()
        self.tick(); self.tick()
        self.assertEqual([c[0] for c in self.api.calls], ["power_on", "power_off"])
        self.assertEqual(self.member.get(self.budget_url).get_json()["used_runs"], 1)

    def test_lost_boot_reply_reconciles_without_second_power_on(self):
        self.prepare()
        self.start(self.stage())
        self.api.lose_reply = True
        self.tick(); self.tick()
        self.advertise()
        self.assertEqual(len(self.api.calls), 1)
        self.assertEqual(self.claim().status_code, 200)

    def test_boot_rejection_and_timeout_refund_only_once(self):
        for rejected in [True, False]:
            # Isolated subcase in the same fixture, refund restores both slots.
            if rejected:
                self.prepare()
            self.api.reject = rejected
            run = self.start(self.stage()).get_json()
            self.tick()
            if not rejected:
                self.api.status_value = "shutdown"  # Unconfirmed start, never replayed.
                with self.app.app_context():
                    pool = db.session.get(JudgePool, self.pool_id)
                    pool.dispatched_at = utcnow() - timedelta(seconds=61)
                    db.session.commit()
                self.tick()
            detail = self.member.get(f"/api/evaluation-runs/{run['id']}").get_json()
            self.assertEqual(detail["status"], "failed")
            self.assertTrue(detail["quota_refunded"])
            with self.app.app_context():
                pool = db.session.get(JudgePool, self.pool_id)
                refund_unstarted(pool, "repeat")
                pool.retry_at = utcnow() - timedelta(seconds=1)
                db.session.commit()
            self.tick()
            self.assertEqual(self.member.get(self.budget_url).get_json()["used_runs"], 0)
        self.assertEqual(len([c for c in self.api.calls if c[0] == "power_on"]), 2)

    def test_running_bad_program_charged_queue_survives_stopping(self):
        self.prepare()
        run = self.start(self.stage()).get_json()
        self.tick(); self.advertise()
        job = self.claim().get_json()
        self.worker.post(f"/api/evaluation-worker/runs/{run['id']}/complete", headers={"X-Evaluation-Lease": job["lease_token"]},
            json={"status": "failed", "error": "invalid contestant program"})
        self.assertEqual(self.member.get(self.budget_url).get_json()["used_runs"], 1)
        with self.app.app_context():
            pool = db.session.get(JudgePool, self.pool_id)
            pool.state = "stopping"
            db.session.commit()
        waiting = self.start(self.stage())
        self.assertEqual(waiting.status_code, 201, waiting.get_json())
        self.assertEqual(self.claim().status_code, 204)
        self.tick(); self.tick()
        self.assertEqual([c[0] for c in self.api.calls], ["power_on", "power_off", "power_on"])

    def test_scheduler_restart_lease_fencing_and_offline_admission(self):
        self.prepare()
        with self.app.app_context():
            pool = db.session.get(JudgePool, self.pool_id)
            pool.checked_at = utcnow() - timedelta(seconds=76)
            pool.lease_owner = "crashed"
            pool.lease_expires_at = utcnow() + timedelta(seconds=5)
            db.session.commit()
            self.assertIsNone(claim_pool("new"))
        self.assertEqual(self.start(self.stage()).status_code, 503)
        self.assertEqual(self.member.get(self.budget_url).get_json()["used_runs"], 0)
        with self.app.app_context():
            pool = db.session.get(JudgePool, self.pool_id)
            pool.lease_expires_at = utcnow() - timedelta(seconds=1)
            db.session.commit()
            self.assertEqual(claim_pool("new"), self.pool_id)
            process_pool(self.pool_id, "crashed", lambda p: self.api)
            self.assertEqual(db.session.get(JudgePool, self.pool_id).lease_owner, "new")
            process_pool(self.pool_id, "new", lambda p: self.api)
        self.assertEqual(self.start(self.stage()).status_code, 201)

    def test_api_outage_unstarted_refund_and_schedule_still_required(self):
        self.prepare()
        run = self.start(self.stage()).get_json()
        self.api.unavailable = True
        with self.app.app_context():
            job = db.session.get(EvaluationRun, run["id"])
            job.created_at = utcnow() - timedelta(seconds=61)
            db.session.commit()
        self.tick()
        self.assertEqual(self.member.get(self.budget_url).get_json()["used_runs"], 0)
        self.api.unavailable = False
        self.tick()
        with self.app.app_context():
            problem = db.session.get(Problem, self.problem["id"])
            problem.track.competition.config = {"launch": {"require_ready": True}}
            db.session.commit()
        blocked = self.start(self.stage())
        self.assertEqual(blocked.status_code, 409, blocked.get_json())
        self.assertIn("schedule", [c["key"] for c in blocked.get_json()["checks"]])
        self.assertEqual(self.member.get(self.budget_url).get_json()["used_runs"], 0)

    def test_admin_only_registration_and_mismatched_runtime_blocked(self):
        self.prepare()
        self.assertEqual(self.member.put(f"/api/manage/problems/{self.problem['id']}/judge", json=self.settings).status_code, 403)
        with self.app.app_context():
            pool = db.session.get(JudgePool, self.pool_id)
            self.assertTrue(available(pool, self.runtime_config))
            self.assertFalse(available(pool, {**self.runtime_config, "image": "image-training"}))
            pool.enabled = False
            db.session.commit()
        self.assertEqual(self.start(self.stage()).status_code, 503)
        self.assertEqual(self.member.get(self.budget_url).get_json()["used_runs"], 0)

    def test_concurrent_schedulers_only_one_owns_instance(self):
        self.prepare()
        with self.app.app_context():
            db.session.get(JudgePool, self.pool_id).checked_at = utcnow() - timedelta(seconds=6)
            db.session.commit()
        def claim(owner):
            with self.app.app_context():
                return claim_pool(owner)
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(claim, ["one", "two"]))
        self.assertEqual(results.count(self.pool_id), 1)

    def test_new_queue_prevents_idle_stop_and_live_run_not_shutdown(self):
        self.prepare()
        self.api.status_value = "running"
        self.advertise()
        with self.app.app_context():
            pool = db.session.get(JudgePool, self.pool_id)
            pool.idle_since = utcnow() - timedelta(seconds=31)
            db.session.commit()
        run = self.start(self.stage()).get_json()
        self.tick()
        self.assertEqual(self.api.calls, [])
        self.claim()
        with self.app.app_context():
            pool = db.session.get(JudgePool, self.pool_id)
            pool.dispatched_at = utcnow() - timedelta(hours=3, seconds=1)
            db.session.commit()
        self.tick()
        self.assertEqual(self.api.calls, [])
        self.assertEqual(self.member.get(f"/api/evaluation-runs/{run['id']}").get_json()["status"], "running")

    def test_trusted_pre_execution_transfer_failure_refunds_but_replay_rejected(self):
        self.prepare()
        run=self.start(self.stage()).get_json()
        self.tick();self.advertise()
        job=self.claim().get_json()
        path=f"/api/evaluation-worker/runs/{run['id']}/complete"
        data={"status":"failed","failure_kind":"infrastructure_before_execution"}
        self.assertEqual(self.member.post(path,json=data).status_code,403)
        response=self.worker.post(path,json=data,headers={"X-Evaluation-Lease":job['lease_token']})
        self.assertEqual(response.status_code,200,response.get_json())
        self.assertTrue(response.get_json()['quota_refunded'])
        self.assertEqual(self.member.get(self.budget_url).get_json()['used_runs'],0)
        self.assertEqual(self.worker.post(path,json=data,headers={"X-Evaluation-Lease":job['lease_token']}).status_code,403)
        self.assertEqual(self.member.get(self.budget_url).get_json()['used_runs'],0)

    def test_bound_problem_cannot_be_deleted_and_unbind_requires_confirmed_shutdown(self):
        self.prepare()
        path=f"/api/manage/problems/{self.problem['id']}/judge"
        self.assertEqual(self.admin.delete(f"/api/manage/problems/{self.problem['id']}").status_code,409)
        self.assertEqual(self.member.delete(path).status_code,403)
        self.assertEqual(self.admin.delete(path).status_code,409)
        self.assertEqual(self.admin.put(path,json={**self.settings,'enabled':False}).status_code,200)
        self.assertEqual(self.admin.delete(path).status_code,409)  # Not yet reconciled.
        self.tick()
        self.assertEqual(self.admin.delete(path).status_code,204)
        with self.app.app_context():
            self.assertIsNone(db.session.get(JudgePool,self.pool_id))


class OriginTransportTests(unittest.TestCase):
    def test_origin_route_keeps_api_hostname_and_does_not_change_other_hosts(self):
        from evaluation_transport import install_origin_route
        with patch.dict('os.environ',{'EVALUATION_API_ORIGIN_IP':'47.84.83.142','EVALUATION_API_BASE':'https://uestcai.top/api'}):
            with patch('socket.getaddrinfo') as resolve:
                install_origin_route()
                import socket
                socket.getaddrinfo('uestcai.top',443)
                resolve.assert_called_with('47.84.83.142',443)
                socket.getaddrinfo('huggingface.co',443)
                resolve.assert_called_with('huggingface.co',443)
                socket.getaddrinfo('uestcai.top',80)
                resolve.assert_called_with('uestcai.top',80)

    def test_bad_origin_rejected(self):
        from evaluation_transport import install_origin_route
        for address in ['127.0.0.1','169.254.169.254','not-ip']:
            with patch.dict('os.environ',{'EVALUATION_API_ORIGIN_IP':address,'EVALUATION_API_BASE':'https://uestcai.top/api'}):
                with self.assertRaises(ValueError):install_origin_route()


if __name__ == "__main__":
    unittest.main()
