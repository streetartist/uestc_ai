import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from evaluation_adapters.libero.scoring import GoalScore, native_goals, validate_scoring
from evaluation_adapters.libero_runner import run_episode, validate_scenarios
from evaluation_adapters.wujie_scenes import libero_scenes
from platform_api.performance_scoring import performance_result
from platform_api.final_evaluation import scene_identity
from platform_api.final_evaluation import validate_libero_final_scenes, LIBERO_FINAL_WEIGHTS
from test_libero_evaluation import Channel, FakeEnv, observation
import test_launch_readiness as launch_fixtures
from platform_api.extensions import db
from platform_api.models import SubmissionVersion
from platform_api.reviewing import review_score, combined_score
from platform_api.problem_setup import validate_runtime
from platform_api.evaluation import ADAPTERS, validate_evaluation_config, validate_metrics


class ScoringTests(unittest.TestCase):
    def test_control_budgets_are_two_times_reference_and_public_scenes_match(self):
        path = Path(__file__).resolve().parents[1] / "deploy/libero/final-request.json"
        scenes = json.loads(path.read_text(encoding="utf-8"))["runtime"]["scenarios"]
        self.assertEqual([s["max_steps"] for s in scenes], [600, 600, 1200, 1400, 2200])
        for scene in scenes:
            self.assertEqual(scene["max_steps"], 2 * max(scene["scoring"]["reference_steps"]))
        self.assertEqual([s["max_steps"] for s in libero_scenes()], [s["max_steps"] for s in scenes[:3]])

    def test_complete_final_request_passes_real_runtime_validation_and_rejects_over_limit(self):
        path = Path(__file__).resolve().parents[1] / "deploy/libero/final-request.json"
        request = json.loads(path.read_text(encoding="utf-8"))
        evaluation = validate_evaluation_config(request["evaluation_config"])
        runtime = validate_runtime(request["runtime"], evaluation)
        self.assertEqual(len(runtime["scenarios"]), 5)
        self.assertEqual(runtime["scenarios"][-1]["max_steps"], 2200)
        request["runtime"]["scenarios"][-1]["max_steps"] = 4000
        self.assertEqual(validate_runtime(request["runtime"], evaluation)["scenarios"][-1]["max_steps"], 4000)
        request["runtime"]["scenarios"][-1]["max_steps"] = 4001
        with self.assertRaises(ValueError):
            validate_runtime(request["runtime"], evaluation)

    def test_final_request_step_counts_are_accepted_by_result_metric_validation(self):
        path = Path(__file__).resolve().parents[1] / "deploy/libero/final-request.json"
        request = json.loads(path.read_text(encoding="utf-8"))
        evaluation = validate_evaluation_config(request["evaluation_config"])
        validate_runtime(request["runtime"], evaluation)
        # A worker job freezes these definitions when it is scheduled.
        evaluation["metric_definitions"] = ADAPTERS["libero-agent-v1"]["metrics"]
        values = {key: 0 for key in evaluation["metrics"]}
        for accepted in (request["runtime"]["scenarios"][-1]["max_steps"], 4000):
            values.update(action_count=accepted, invalid_actions=accepted)
            normalized = validate_metrics(evaluation, values)
            self.assertEqual(normalized["action_count"], accepted)
            self.assertEqual(normalized["invalid_actions"], accepted)
        for metric in ("action_count", "invalid_actions"):
            invalid = {**values, metric: 4001}
            with self.assertRaisesRegex(ValueError, metric):
                validate_metrics(evaluation, invalid)

    def test_reference_curve_and_two_equal_targets(self):
        for time, expected in ((50, 100), (100, 80), (150, 60), (200, 60)):
            scorer = GoalScore([100, 100])
            for step in range(1, time + 1):
                scorer.update(step, [step == time, step == time])
            self.assertEqual(scorer.result()["automatic_score"], expected)

    def test_partial_complete_missing_target_cannot_gain_speed(self):
        scorer = GoalScore([100, 200])
        for step in range(1, 101):
            scorer.update(step, [step >= 50, False])
        self.assertEqual(scorer.result(), {"completion_score": 30, "efficiency_score": 20, "automatic_score": 50})
        self.assertIsNone(scorer.targets()[1]["attainment_step"])

    def test_lost_placement_loses_points_and_recovery_resets_time(self):
        scorer = GoalScore([100, 100])
        for step in range(1, 101):
            scorer.update(step, [step >= 10 and step < 50, False])
        self.assertEqual(scorer.result()["automatic_score"], 0)
        scorer.update(101, [True, False])
        self.assertEqual(scorer.targets()[0]["attainment_step"], 101)
        self.assertAlmostEqual(scorer.result()["automatic_score"], 39.8)

    def test_targets_use_cumulative_steps_even_if_completed_second(self):
        scorer = GoalScore([100, 200])
        for step in range(1, 201):
            scorer.update(step, [step >= 100, step >= 200])
        self.assertEqual([t["attainment_step"] for t in scorer.targets()], [100, 200])
        self.assertEqual(scorer.result()["automatic_score"], 80)

    def test_native_predicates_match_goal_count(self):
        core = SimpleNamespace(parsed_problem={"goal_state": [["on", "bowl", "plate"], ["in", "can", "basket"]]},
                               _eval_predicate=lambda goal: goal[1] == "bowl")
        self.assertEqual(native_goals(SimpleNamespace(env=core), 2), [True, False])
        with self.assertRaises(ValueError): native_goals(SimpleNamespace(env=core), 1)

    def test_reference_steps_are_frozen_but_do_not_create_new_scene_identity(self):
        scene = libero_scenes()[0]
        scene["scoring"] = {"version": 2, "reference_steps": [100]}
        validate_scenarios([scene], 1)
        other = {**scene, "scoring": {"version": 2, "reference_steps": [200]}}
        self.assertEqual(scene_identity(scene), scene_identity(other))
        for invalid in ([], [0], [True], [1, 2, 3]):
            with self.assertRaises(ValueError): validate_scoring({"version": 2, "reference_steps": invalid})

    def test_two_final_scores_include_failed_scene(self):
        config = {"preset": "wujie-libero-v2", "criterion": "表现"}
        result = performance_result(config, "libero-agent-v1", [
            {"completion_score": 60, "efficiency_score": 40, "automatic_score": 100},
            {"completion_score": 0, "efficiency_score": 0, "automatic_score": 0}])
        self.assertEqual(result["score"], 50)
        with self.assertRaises(ValueError):
            performance_result(config, "libero-agent-v1", [{"completion_score": 0, "efficiency_score": 40, "automatic_score": 40}])

    def test_real_episode_hook_scores_native_goals_without_exposing_truth(self):
        class Environment(FakeEnv):
            def __init__(self):
                super().__init__(2)
                self.env = SimpleNamespace(parsed_problem={"goal_state": [["on", "bowl", "plate"]]},
                                           _eval_predicate=lambda _: self.check_success())
        scene = libero_scenes()[0]
        scene.update(max_steps=3, scoring={"version": 2, "reference_steps": [4]})
        channel = Channel([[0] * 7] * 3)
        with tempfile.TemporaryDirectory() as directory:
            result = run_episode(channel, Environment(), observation(), "task", scene,
                ["completion_score", "efficiency_score", "automatic_score"], Path(directory))
            self.assertEqual(result["automatic_score"], 100)
            self.assertTrue((Path(directory) / "trajectory.json").is_file())
        for message in channel.messages:
            self.assertNotIn("scoring", message.get("goal", {}))
            self.assertNotIn("targets", message.get("observation", {}))

    def test_five_task_weights_include_public_tasks_and_failed_tasks(self):
        config = {"preset": "wujie-libero-v2", "criterion": "表现"}
        for index, expected in enumerate((100 / 6, 100 / 6, 100 / 6, 25, 25)):
            episodes = [{"completion_score": 60 if i == index else 0,
                         "efficiency_score": 40 if i == index else 0,
                         "automatic_score": 100 if i == index else 0} for i in range(5)]
            self.assertAlmostEqual(performance_result(config, "libero-agent-v1", episodes,
                                  LIBERO_FINAL_WEIGHTS)["score"], expected)
        complete = [{"completion_score": 60, "efficiency_score": 0, "automatic_score": 60}] * 5
        self.assertEqual(performance_result(config, "libero-agent-v1", complete, LIBERO_FINAL_WEIGHTS)["score"], 60)
        with self.assertRaises(ValueError):
            performance_result(config, "libero-agent-v1", complete[:4], LIBERO_FINAL_WEIGHTS)

    def test_final_includes_three_public_tasks_but_hidden_scenes_cannot_repeat_them(self):
        public = libero_scenes()
        scenes = [{**s, "scoring": {"version": 2, "reference_steps": [100] * count}}
                  for s, count in zip(public, (1, 1, 2))]
        scenes += [{**public[2], "id": "hidden-" + str(i), "init_state_id": 40 + i,
                    "scoring": {"version": 2, "reference_steps": [100, 200]}} for i in range(2)]
        validate_libero_final_scenes(scenes, public)
        with self.assertRaises(ValueError): validate_libero_final_scenes(scenes[:2], public)
        with self.assertRaises(ValueError): validate_libero_final_scenes([*scenes[:3], scenes[2], scenes[4]], public)


class PassFailReviewTests(unittest.TestCase):
    setUp = launch_fixtures.LaunchTests.setUp
    tearDown = launch_fixtures.LaunchTests.tearDown
    login = launch_fixtures.LaunchTests.login
    register = launch_fixtures.LaunchTests.register
    fixture = launch_fixtures.LaunchTests.fixture
    claim = launch_fixtures.LaunchTests.claim
    stage = launch_fixtures.LaunchTests.stage
    start = launch_fixtures.LaunchTests.start
    formal = launch_fixtures.LaunchTests.formal
    prepare_scoring = launch_fixtures.LaunchTests.prepare_scoring

    def test_report_keeps_its_ten_points_with_code_gate_and_locked_performance(self):
        self.prepare_scoring()
        response = self.admin.patch(f"/api/manage/problems/{self.problem['id']}", json={
            "scoring_config": {"review_score_mode": "weighted", "performance_scoring": self.scoring,
                               "code_review_mode": "pass_fail", "external_weight_percent": 40},
            "judging_schema": {"rubric": {"表现": 5 / 6, "报告": 1 / 6}}})
        self.assertEqual(response.status_code, 200, response.get_json())
        asset = self.stage()
        self.assertEqual(self.start(asset).status_code, 201)
        job = self.claim()
        result = self.worker.post(f"/api/evaluation-worker/runs/{job['id']}/complete",
            headers={"X-Evaluation-Lease": job["lease_token"]}, json={"status": "completed", "episodes": [
                {"task_success": 80, "exploration_progress": 80}]})
        self.assertEqual(result.status_code, 200, result.get_json())
        self.assertEqual(result.get_json()["performance"]["score"], 80)
        version = self.formal(asset, job["id"]).get_json()["version"]["id"]
        self.login(self.reviewer, "reviewer@uestc.ai")
        payload = {"submission_version_id": version, "scores": {"表现": 999},
                   "code_review_passed": True, "total_score": 999, "status": "submitted"}
        self.assertEqual(self.reviewer.post("/api/reviews", json=payload).status_code, 400)
        for invalid in (-1, 101, "90"):
            payload["scores"]["报告"] = invalid
            self.assertEqual(self.reviewer.post("/api/reviews", json=payload).status_code, 400)
        payload["scores"] = {"表现": 999, "报告": 90, "__code_review_passed": 1}
        payload.update(code_review_passed=False, feedback_md="程序读取了隐藏场景文件。")
        rejected = self.reviewer.post("/api/reviews", json=payload)
        self.assertEqual(rejected.status_code, 201, rejected.get_json())
        self.assertEqual(rejected.get_json()["scores"]["__code_review_passed"], 0)
        with self.app.app_context():
            record = db.session.get(SubmissionVersion, version)
            competition_id = record.submission.problem.track.competition_id
            self.assertIsNone(review_score(record))
        imported = self.admin.post("/api/scores/import", json={"competition_id": competition_id,
            "problem_id": self.problem["id"], "source": "onsite-defense", "label": "现场答辩", "status": "confirmed",
            "records": [{"submission_version_id": version, "total_score": 70}]})
        self.assertEqual(imported.status_code, 201, imported.get_json())
        with self.app.app_context():
            self.assertIsNone(combined_score(db.session.get(SubmissionVersion, version)))
        payload["code_review_passed"] = True
        saved = self.reviewer.post("/api/reviews", json=payload)
        self.assertEqual(saved.status_code, 201, saved.get_json())
        self.assertEqual(saved.get_json()["scores"]["表现"], 80)
        self.assertEqual(saved.get_json()["scores"]["报告"], 90)
        self.assertAlmostEqual(saved.get_json()["total_score"], 490 / 6)
        with self.app.app_context():
            self.assertAlmostEqual(combined_score(db.session.get(SubmissionVersion, version))["total_score"], 77)

    def test_review_is_a_gate_and_cannot_change_automatic_score(self):
        self.prepare_scoring()
        response = self.admin.patch(f"/api/manage/problems/{self.problem['id']}", json={
            "scoring_config": {"review_score_mode": "weighted", "performance_scoring": self.scoring,
                               "code_review_mode": "pass_fail"},
            "judging_schema": {"rubric": {"表现": 1}}})
        self.assertEqual(response.status_code, 200, response.get_json())
        asset = self.stage()
        self.assertEqual(self.start(asset).status_code, 201)
        job = self.claim()
        self.worker.post(f"/api/evaluation-worker/runs/{job['id']}/complete",
            headers={"X-Evaluation-Lease": job["lease_token"]}, json={"status": "completed", "episodes": [
                {"task_success": 0, "exploration_progress": 50}]})
        formal = self.formal(asset, job["id"]).get_json()
        version = formal["version"]["id"]
        self.login(self.reviewer, "reviewer@uestc.ai")
        payload = {"submission_version_id": version, "scores": {"表现": 999}, "status": "submitted"}
        self.assertEqual(self.reviewer.post("/api/reviews", json=payload).status_code, 400)
        payload["code_review_passed"] = False
        self.assertEqual(self.reviewer.post("/api/reviews", json=payload).status_code, 400)
        payload["feedback_md"] = "读取了隐藏场景文件。"
        self.assertEqual(self.reviewer.post("/api/reviews", json=payload).status_code, 201)
        with self.app.app_context():
            self.assertIsNone(review_score(db.session.get(SubmissionVersion, version)))
        payload["code_review_passed"] = True
        saved = self.reviewer.post("/api/reviews", json=payload)
        self.assertEqual(saved.status_code, 201, saved.get_json())
        self.assertEqual(saved.get_json()["total_score"], 35)
        with self.app.app_context():
            self.assertEqual(review_score(db.session.get(SubmissionVersion, version))["total_score"], 35)


if __name__ == "__main__": unittest.main()
