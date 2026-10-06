import sys
import unittest
from copy import deepcopy
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_backend_api as fixtures
import test_independent_evaluation_trials as trial_fixtures
from platform_api.extensions import db
from platform_api.models import Competition, Problem, utcnow
from platform_api.performance_scoring import performance_result


class PerformanceTests(unittest.TestCase):
    def result(self, preset, adapter, metrics):
        return performance_result({"preset": preset, "criterion": "表现"}, adapter, [{"metrics": metrics}])["score"]

    def test_classification_accuracy_gates_efficiency_and_f1_counts(self):
        self.assertEqual(self.result("wujie-depth-v1", "classification-v1", {"accuracy":0,"macro_f1":0,"latency_ms":1}), 0)
        self.assertEqual(self.result("wujie-depth-v1", "classification-v1", {"accuracy":100,"macro_f1":100,"latency_ms":100}), 100)
        self.assertEqual(self.result("wujie-depth-v1", "classification-v1", {"accuracy":80,"macro_f1":60,"latency_ms":200}), 74)

    def test_world_progress_arm_partial_credit_and_invalid_metrics(self):
        self.assertEqual(self.result("wujie-world-v1", "minecraft-agent-v1", {"task_success":0,"exploration_progress":50}), 35)
        self.assertEqual(self.result("wujie-arm-v1", "robot-arm-agent-v1", {"task_success":0,"stable_grasps":100,"action_count":0}), 10)
        self.assertEqual(self.result("wujie-arm-v1", "robot-arm-agent-v1", {"task_success":100,"stable_grasps":100,"action_count":60}), 99)
        for value in (float("nan"), -1, 101, True):
            with self.assertRaises(ValueError):
                self.result("wujie-world-v1", "minecraft-agent-v1", {"task_success":value,"exploration_progress":50})


class LaunchTests(unittest.TestCase):
    setUp = fixtures.PlatformApiTestCase.setUp
    tearDown = fixtures.PlatformApiTestCase.tearDown
    login = fixtures.PlatformApiTestCase.login
    register = fixtures.PlatformApiTestCase.register
    fixture = trial_fixtures.IndependentTrialTests.fixture
    claim = trial_fixtures.IndependentTrialTests.claim
    stage = trial_fixtures.IndependentTrialTests.stage
    start = trial_fixtures.IndependentTrialTests.start
    formal = trial_fixtures.IndependentTrialTests.formal

    def prepare_scoring(self):
        self.fixture()
        self.config["metrics"] = ["task_success", "exploration_progress"]
        self.scoring = {"preset":"wujie-world-v1","criterion":"表现"}
        response = self.admin.patch(f"/api/manage/problems/{self.problem['id']}", json={
            "evaluation_config":self.config,"scoring_config":{"review_score_mode":"weighted","performance_scoring":self.scoring},
            "judging_schema":{"rubric":{"表现":.8,"报告":.2}}})
        self.assertEqual(response.status_code, 200, response.get_json())

    def test_trusted_score_cannot_be_overridden_and_rules_freeze_after_trial(self):
        self.prepare_scoring()
        asset = self.stage()
        started = self.start(asset)
        self.assertEqual(started.status_code, 201, started.get_json())
        job = self.claim()
        result = self.worker.post(f"/api/evaluation-worker/runs/{job['id']}/complete", headers={"X-Evaluation-Lease":job["lease_token"]},
            json={"status":"completed","episodes":[{"task_success":0,"exploration_progress":50}]})
        self.assertEqual(result.status_code, 200, result.get_json())
        self.assertEqual(result.get_json()["performance"]["score"], 35)
        formal = self.formal(asset, job["id"])
        self.assertEqual(formal.status_code, 201, formal.get_json())
        self.login(self.reviewer, "reviewer@uestc.ai")
        response = self.reviewer.post("/api/reviews", json={"submission_version_id":formal.get_json()["version"]["id"],
            "scores":{"表现":999,"报告":80},"total_score":999})
        self.assertEqual(response.status_code, 201, response.get_json())
        self.assertEqual(response.get_json()["scores"]["表现"], 35)
        self.assertEqual(response.get_json()["total_score"], 44)
        self.assertEqual(self.admin.patch(f"/api/manage/problems/{self.problem['id']}", json={"scoring_config":{"review_score_mode":"weighted"}}).status_code, 409)
        self.assertEqual(self.admin.patch(f"/api/manage/problems/{self.problem['id']}", json={"title":"说明更新"}).status_code, 200)
        self.assertEqual(self.admin.patch(f"/api/manage/problems/{self.problem['id']}", json={"judging_schema":{"rubric":{"表现":.7,"报告":.3}}}).status_code, 409)

    def test_missing_launch_environment_and_schedule_never_charge_attempt(self):
        self.prepare_scoring()
        with self.app.app_context():
            problem = db.session.get(Problem, self.problem["id"])
            competition = problem.track.competition
            competition.config = {**competition.config,"launch":{"require_ready":True}}
            competition.starts_at = None
            competition.ends_at = None
            db.session.commit()
            competition_id = competition.id
        response = self.start(self.stage())
        self.assertEqual(response.status_code, 409, response.get_json())
        self.assertEqual(self.member.get(self.budget_url).get_json()["used_runs"], 0)
        check = self.admin.get(f"/api/manage/competitions/{competition_id}/launch-check")
        self.assertEqual(check.status_code, 200)
        self.assertFalse(check.get_json()["ready"])
        self.assertEqual(self.member.get(f"/api/manage/competitions/{competition_id}/launch-check").status_code, 403)
        with self.app.app_context():
            comp = db.session.get(Competition, competition_id)
            comp.starts_at = utcnow() + timedelta(days=1)
            comp.ends_at = utcnow() + timedelta(days=2)
            db.session.commit()
        self.assertEqual(self.start(self.stage()).status_code, 409)
        self.assertEqual(self.member.get(self.budget_url).get_json()["used_runs"], 0)

    def test_mismatched_score_preset_rejected_before_saving(self):
        self.prepare_scoring()
        bad = deepcopy(self.scoring); bad["preset"] = "wujie-depth-v1"
        response = self.admin.patch(f"/api/manage/problems/{self.problem['id']}", json={"scoring_config":{"review_score_mode":"weighted","performance_scoring":bad}})
        self.assertEqual(response.status_code, 400)

    def test_ready_runtime_and_current_schedule_allow_one_trial(self):
        self.prepare_scoring()
        self.config["task"] = "open-world"
        runtime = {"image":"sha256:" + "a" * 64,"agent_image":"sha256:" + "b" * 64,
            "scenarios":[{"id":"private-test","label":"资源采集","difficulty":"beginner",
                "task_id":"open-ended","world_seed":71231,"max_steps":600,"goals":["log"]}]}
        saved = self.admin.put(f"/api/manage/problems/{self.problem['id']}/setup", json={"evaluation_config":self.config,"runtime":runtime})
        self.assertEqual(saved.status_code, 200, saved.get_json())
        with self.app.app_context():
            problem = db.session.get(Problem, self.problem["id"])
            comp = problem.track.competition
            comp.config = {**comp.config,"launch":{"require_ready":True}}
            comp.starts_at, comp.ends_at = utcnow() - timedelta(hours=1), utcnow() + timedelta(days=1)
            db.session.commit()
        headers = {"Authorization":"Bearer test-worker"}
        advertised = self.worker.post("/api/evaluation-worker/claim", headers=headers,
            json={"adapters":["minecraft-agent-v1"],"gpu":False,"managed_runtime":True,"worker_id":"launch-worker"})
        self.assertEqual(advertised.status_code, 204)
        checks = self.admin.get(f"/api/manage/problems/{self.problem['id']}/setup").get_json()
        self.assertTrue(checks["ready"], checks)
        self.assertEqual(self.start(self.stage()).status_code, 201)
        self.assertEqual(self.member.get(self.budget_url).get_json()["used_runs"], 1)


if __name__ == "__main__": unittest.main()
