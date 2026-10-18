import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from evaluation_adapters.libero_scene_pack import load_scene_asset
from evaluation_adapters.libero_runner import validate_scenarios
from evaluation_adapters.wujie_scenes import libero_scenes


class ScenePackTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.scene = {**libero_scenes()[0], "scene_asset": "bowl-variant", "task_name": "pick_the_other_bowl"}
        (self.root / "scene.bddl").write_text("(:language Pick the other bowl)", encoding="utf-8")
        np.save(self.root / "states.npy", np.ones((2, 8)))
        self.asset = {"suite": self.scene["suite"], "task_id": 2, "task_name": self.scene["task_name"],
                      "instruction": "Pick the other bowl", "bddl": "scene.bddl", "states": "states.npy"}
        self.save_manifest()

    def save_manifest(self):
        for field in ("bddl", "states"):
            self.asset[field + "_sha256"] = hashlib.sha256((self.root / self.asset[field]).read_bytes()).hexdigest()
        (self.root / "manifest.json").write_text(json.dumps({"version": 1, "scenes": {"bowl-variant": self.asset}}))

    def test_existing_scenes_and_new_asset_keep_same_protocol(self):
        validate_scenarios(libero_scenes(), 3)
        validate_scenarios([self.scene], 1)
        path, state, language = load_scene_asset(self.scene, self.root)
        self.assertEqual(path.name, "scene.bddl")
        self.assertEqual(state.shape, (8,))
        self.assertEqual(language, "Pick the other bowl")
        with self.assertRaises(ValueError):
            validate_scenarios([{**self.scene, "object_pose": [0, 0, 0]}], 1)

    def test_hash_and_public_identity_must_match(self):
        with self.assertRaisesRegex(ValueError, "identity"):
            load_scene_asset({**self.scene, "task_name": "old_task"}, self.root)
        (self.root / "scene.bddl").write_text("changed")
        with self.assertRaisesRegex(ValueError, "checksum"):
            load_scene_asset(self.scene, self.root)

    def test_instruction_and_goal_name_must_describe_loaded_task(self):
        self.asset["instruction"] = "old instruction"
        self.save_manifest()
        with self.assertRaisesRegex(ValueError, "instruction"):
            load_scene_asset(self.scene, self.root)
        self.asset["instruction"] = "Pick the other bowl"
        self.asset["task_name"] = self.scene["task_name"] = "old_task"
        self.save_manifest()
        with self.assertRaisesRegex(ValueError, "task name"):
            load_scene_asset(self.scene, self.root)

    def test_non_numeric_pickle_and_nan_states_rejected(self):
        for array in (np.array([[object()]], dtype=object), np.array([[float("nan")]]), np.ones(8)):
            np.save(self.root / "states.npy", array)
            self.save_manifest()
            with self.assertRaises(ValueError):
                load_scene_asset(self.scene, self.root)

    def test_state_index_and_asset_path_rejected(self):
        with self.assertRaisesRegex(ValueError, "unavailable"):
            load_scene_asset({**self.scene, "init_state_id": 2}, self.root)
        self.asset["bddl"] = "../outside.bddl"
        (self.root / "manifest.json").write_text(json.dumps({"version": 1, "scenes": {"bowl-variant": self.asset}}))
        with self.assertRaisesRegex(ValueError, "path"):
            load_scene_asset(self.scene, self.root)
        with self.assertRaises(ValueError):
            validate_scenarios([{**self.scene, "scene_asset": "../outside"}], 1)


if __name__ == "__main__":
    unittest.main()
