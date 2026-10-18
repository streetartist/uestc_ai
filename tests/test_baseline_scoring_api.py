"""Real API/DB wiring with deterministic worker metrics, not GPU acceptance."""
import sys
import unittest
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_backend_api as fixtures
import test_independent_evaluation_trials as trials


class BaselineScoreAPITests(unittest.TestCase):
    setUp = fixtures.PlatformApiTestCase.setUp
    tearDown = fixtures.PlatformApiTestCase.tearDown
    login = fixtures.PlatformApiTestCase.login
    register = fixtures.PlatformApiTestCase.register
    fixture = trials.IndependentTrialTests.fixture
    stage = trials.IndependentTrialTests.stage
    start = trials.IndependentTrialTests.start
    claim = trials.IndependentTrialTests.claim
    formal = trials.IndependentTrialTests.formal

    def test_frozen_reference_selected_result_and_trusted_review(self):
        self.fixture(adapter="classification-v1", limit=3)
        self.config["metrics"] = ["accuracy", "macro_f1", "latency_ms"]
        scoring = {"preset": "wujie-depth-baseline-v2", "criterion": "项目成果",
                   "reference": {"baseline_accuracy": 60, "gain_span_pp": 10, "latency_ms": 100}}
        body = {"evaluation_config": self.config, "scoring_config": {"review_score_mode": "weighted", "performance_scoring": scoring},
                "judging_schema": {"rubric": {"项目成果": .8, "技术说明": .2}}}
        response = self.admin.patch(f"/api/manage/problems/{self.problem['id']}", json=body)
        self.assertEqual(response.status_code, 200, response.get_json())
        asset = self.stage()
        started = self.start(asset)
        self.assertEqual(started.status_code, 201, started.get_json())
        job = self.claim()
        changed = deepcopy(body)
        changed["scoring_config"]["performance_scoring"]["reference"]["baseline_accuracy"] = 65
        self.assertEqual(self.admin.patch(f"/api/manage/problems/{self.problem['id']}", json=changed).status_code, 409)
        response = self.worker.post(f"/api/evaluation-worker/runs/{job['id']}/complete",
            headers={"X-Evaluation-Lease": job["lease_token"]},
            json={"status": "completed", "episodes": [{"accuracy": 65, "macro_f1": 60, "latency_ms": 200}]})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()["performance"]["score"], 81)
        self.assertEqual(response.get_json()["performance"]["reference"], scoring["reference"])
        submitted = self.formal(asset, job["id"])
        self.assertEqual(submitted.status_code, 201, submitted.get_json())
        self.assertEqual(self.member.get(self.budget_url).get_json()["used_runs"], 1)
        self.login(self.reviewer, "reviewer@uestc.ai")
        review = self.reviewer.post("/api/reviews", json={"submission_version_id": submitted.get_json()["version"]["id"],
            "scores": {"项目成果": 1, "技术说明": 80}, "total_score": 1})
        self.assertEqual(review.status_code, 201, review.get_json())
        self.assertEqual(review.get_json()["scores"]["项目成果"], 81)
        self.assertAlmostEqual(review.get_json()["total_score"], 80.8)


class ReleaseScoreWeightsTests(unittest.TestCase):
    def test_release_config_composes_objective_report_and_defense(self):
        import json
        from types import SimpleNamespace
        from unittest.mock import patch
        from platform_api.reviewing import calculate_review_total, combined_score
        from platform_api.performance_scoring import validate_problem_scoring
        config = json.loads((Path(__file__).resolve().parents[1] / "docs/cuhk-v2-release-config.template.json").read_text("utf-8"))["problem_patch"]
        # Deployment fills measured references; weights come unchanged from the release template.
        config["scoring_config"]["performance_scoring"]["reference"] = {"baseline_accuracy": 60, "gain_span_pp": 15, "latency_ms": 100}
        validate_problem_scoring(config["scoring_config"], config["evaluation_config"], config["judging_schema"])
        problem = SimpleNamespace(scoring_config=config["scoring_config"], judging_schema=config["judging_schema"])
        version = SimpleNamespace(submission=SimpleNamespace(problem=problem))
        for objective, report, defense, expected in [(100,0,0,50), (0,100,0,10), (0,0,100,40), (100,100,100,100), (81,80,90,84.5)]:
            with self.subTest(objective=objective, report=report, defense=defense):
                stage = calculate_review_total(problem, {"模型或智能体实际表现": objective, "技术路线说明": report})
                with patch("platform_api.reviewing.review_score", return_value={"total_score": stage, "reviewed_count": 1, "reviewer_count": 1}), patch("platform_api.reviewing.latest_external_score", return_value=SimpleNamespace(total_score=defense)):
                    result = combined_score(version)
                self.assertAlmostEqual(result["total_score"], expected)


if __name__ == "__main__":
    unittest.main()
