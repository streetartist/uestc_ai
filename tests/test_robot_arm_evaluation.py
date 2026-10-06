import math
import sys
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from build_robot_arm_package import default_scenes
from evaluation_adapters.robot_arm_runner import PhysicalVerifier, checked_command, validate_scenarios
from platform_api.evaluation import validate_evaluation_config
from platform_api.problem_setup import validate_runtime


class RobotArmTests(unittest.TestCase):
    def test_scenes_and_runtime_reject_bad_private_configuration(self):
        scenes = default_scenes()
        validate_scenarios(scenes, 3)
        config = {"adapter": "robot-arm-agent-v1", "task": "multi-step",
                  "resources": {"cpus": 2, "memory_mb": 4096, "gpu": False, "time_seconds": 900, "episodes": 3},
                  "api": {"enabled": False, "max_calls": 0}, "metrics": ["task_success", "stable_seconds"]}
        validate_evaluation_config(config)
        runtime = {"image": "sha256:" + "a" * 64, "agent_image": "sha256:" + "b" * 64, "scenarios": scenes}
        self.assertEqual(validate_runtime(runtime, config), runtime)
        with self.assertRaises(ValueError): validate_runtime({**runtime, "agent_image": ""}, config)
        for key, value in (("seed", True), ("max_steps", 0), ("hold_steps", 1), ("task", "teleport")):
            bad = deepcopy(scenes); bad[0][key] = value
            with self.assertRaises(ValueError): validate_scenarios(bad, 3)
        bad = deepcopy(scenes); bad[1]["target"] = [math.nan, .1]
        with self.assertRaises(ValueError): validate_scenarios(bad, 3)
        bad = deepcopy(scenes); bad[0]["placements"] = {"red": [0, 0], "green": [0, 0]}
        with self.assertRaises(ValueError): validate_scenarios(bad, 3)
        bad = deepcopy(config); bad["resources"]["gpu"] = True
        with self.assertRaises(ValueError): validate_evaluation_config(bad)

    def test_action_surface_is_bounded_and_cannot_change_simulator_or_results(self):
        valid = {"target": [.1, .1, .9], "gripper": 1, "repeat": 20}
        self.assertEqual(checked_command({"type": "action", "value": valid}), valid)
        for value in ([0]*7, {**valid, "repeat": 21}, {**valid, "target": [0, 0, .1]},
                      {**valid, "task_success": 100}, {"delta": [math.nan]*7}, {"delta": [2]*7},
                      {"code": "env.sim.data.qpos[:] = 0"}):
            self.assertIsNone(checked_command({"type": "action", "value": value}))

    def test_lift_requires_physical_grasp_and_continuous_hold(self):
        v = PhysicalVerifier(default_scenes()[0])
        for _ in range(20): v.update([0, 0, .9], [.1, .1, .825], False, False)
        self.assertEqual(v.metrics(1)["task_success"], 0)
        for _ in range(9): v.update([0, 0, .9], [.1, .1, .825], True, False)
        self.assertEqual(v.metrics(1)["task_success"], 0)
        v.update([0, 0, .9], [.1, .1, .825], True, False)
        self.assertEqual(v.metrics(1)["task_success"], 100)

    def test_stack_cannot_succeed_from_initial_placement_or_agent_claim(self):
        v = PhysicalVerifier(default_scenes()[2])
        for _ in range(10): v.update([.1, .1, .866], [.1, .1, .826], False, True)
        self.assertFalse(v.ever_success)
        v.update([0, 0, .95], [.1, .1, .826], True, False)
        for _ in range(10): v.update([.1, .1, .866], [.1, .1, .826], False, True, False)
        self.assertEqual(v.metrics(1)["task_success"], 100)
        self.assertEqual(v.metrics(1)["recovery_success"], 0)

    def test_release_is_not_a_drop_and_recovery_requires_regrasp_hold(self):
        v = PhysicalVerifier(default_scenes()[1])
        for _ in range(10): v.update([0, 0, .95], [.1, .1, .825], True, False)
        v.update([0, 0, .95], [.1, .1, .825], False, False, True)
        self.assertEqual(v.drops, 1)
        for _ in range(9): v.update([0, 0, .95], [.1, .1, .825], True, False)
        self.assertEqual(v.metrics(1)["recovery_success"], 0)
        v.update([0, 0, .95], [.1, .1, .825], True, False)
        self.assertEqual(v.metrics(1)["recovery_success"], 100)
        v.update([.12, .1, .82], [.1, .1, .825], False, False, False)
        self.assertEqual(v.drops, 1)

    def test_worker_routes_robot_to_two_containers_with_private_scenes(self):
        from evaluation_worker import execute
        config = {"adapter": "robot-arm-agent-v1", "task": "multi-step", "resources": {"gpu": False, "memory_mb": 2048}}
        job = {"config": config, "runtime": {"image": "sha256:" + "a" * 64,
            "agent_image": "sha256:" + "b" * 64, "scenarios": default_scenes()}}
        with patch("evaluation_worker.execute_isolated_agent", return_value={"status": "completed"}) as runner:
            self.assertEqual(execute("base", job, {}, "", "", "")["status"], "completed")
            self.assertEqual(runner.call_args.args[3], job["runtime"]["agent_image"])
            self.assertIn("robot-scenes-", runner.call_args.args[4])
        with self.assertRaisesRegex(RuntimeError, "problem runtime"):
            execute("base", {"config": config}, {"robot-arm-agent-v1": "sha256:"+"a"*64}, "", "", "")


if __name__ == "__main__":
    unittest.main()
