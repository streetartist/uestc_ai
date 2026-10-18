"""Candidate fixed-reference scoring; fixtures are illustrative, not contest constants."""
import sys
import unittest
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from platform_api.performance_scoring import performance_result, validate_performance_scoring, validate_problem_scoring


class BaselineScoringTests(unittest.TestCase):
    def setUp(self):
        self.config = {"preset": "wujie-depth-baseline-v2", "criterion": "项目成果",
                       "reference": {"baseline_accuracy": 60, "gain_span_pp": 10, "latency_ms": 100}}

    def score(self, accuracy, f1, latency):
        return performance_result(self.config, "classification-v1", [{"accuracy": accuracy, "macro_f1": f1, "latency_ms": latency}])

    def test_boundaries_and_components(self):
        self.assertEqual(self.score(0, 0, 1)["score"], 0)
        self.assertEqual(self.score(100, 100, 100)["score"], 100)
        self.assertEqual(self.score(60, 50, 100)["score"], 75)
        self.assertEqual(self.score(65, 60, 200)["components"], {"baseline": 60, "improvement": 10, "macro_f1": 6, "efficiency": 5})
        self.assertEqual(self.score(30, 20, 100)["score"], 37)
        self.assertEqual(self.score(70, 65, 100)["score"], 96.5)

    def test_monotonic_bounded_and_no_intermediate_rounding(self):
        previous = -1
        for accuracy in range(101):
            result = self.score(accuracy, accuracy, 137)
            self.assertGreaterEqual(result["score"], previous)
            self.assertLessEqual(result["score"], 100)
            previous = result["score"]
        value = self.score(61.23456789, 51.23456789, 137)
        self.assertEqual(value["score"], sum(value["components"].values()))
        self.assertNotEqual(value["score"], round(value["score"], 6))
        self.assertGreater(self.score(60, 55, 100)["score"], self.score(60, 55, 200)["score"])

    def test_reference_validation_and_reachable_gain(self):
        for invalid in [None, [], "", {"unexpected": 1}]:
            config = deepcopy(self.config)
            config["reference"] = invalid
            with self.assertRaises(ValueError):
                validate_performance_scoring(config)
        with self.assertRaises(ValueError):
            validate_performance_scoring({"preset": [], "criterion": "项目成果"})
        for field, values in {"baseline_accuracy": [0, 100, True, float("nan")],
                              "gain_span_pp": [0, 41, float("inf")], "latency_ms": [0, -1, "100"]}.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    config = deepcopy(self.config)
                    config["reference"][field] = value
                    with self.assertRaises(ValueError):
                        validate_performance_scoring(config)
        config = deepcopy(self.config)
        config["reference"].update(baseline_accuracy=95, gain_span_pp=5)
        validate_performance_scoring(config)
        returned = validate_performance_scoring(config)
        returned["reference"]["baseline_accuracy"] = 1
        self.assertEqual(config["reference"]["baseline_accuracy"], 95)

    def test_invalid_metrics_and_aggregate_contract(self):
        for accuracy, f1, latency in [(101, 50, 1), (50, -1, 1), (50, 50, 0), (True, 50, 1), (50, float("nan"), 1)]:
            with self.assertRaises(ValueError):
                self.score(accuracy, f1, latency)
        valid = {"accuracy": 60, "macro_f1": 50, "latency_ms": 100}
        for adapter, episodes in [("other", [valid]), ("classification-v1", [valid, valid]), ("classification-v1", [])]:
            with self.assertRaises(ValueError):
                performance_result(self.config, adapter, episodes)

    def test_legacy_score_and_config_unchanged(self):
        old = {"preset": "wujie-depth-v1", "criterion": "表现"}
        self.assertEqual(performance_result(old, "classification-v1", [{"accuracy": 80, "macro_f1": 60, "latency_ms": 200}])["score"], 74)
        with self.assertRaises(ValueError):
            validate_performance_scoring({**old, "reference": self.config["reference"]})

    def test_weighted_rubric_contract(self):
        config = {"performance_scoring": self.config, "review_score_mode": "weighted"}
        evaluation = {"adapter": "classification-v1", "metrics": ["accuracy", "macro_f1", "latency_ms"]}
        validate_problem_scoring(config, evaluation, {"rubric": {"项目成果": .8, "技术说明": .2}})
        with self.assertRaises(ValueError):
            validate_problem_scoring(config, evaluation, {"rubric": {"项目成果": .6, "技术说明": .2}})


if __name__ == "__main__":
    unittest.main()
