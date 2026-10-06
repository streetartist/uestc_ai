import json
import contextlib
import io
import os
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from build_depth_competition_image_bundle import build
import build_classification_package
from build_wujie_starters import build as build_starters


class DepthImageBundleTests(unittest.TestCase):
    def test_all_three_public_starters_are_complete_without_judge_data(self):
        with tempfile.TemporaryDirectory() as directory:
            packages = build_starters(Path(directory))
            self.assertEqual({p.name for p in packages}, {f"wujie-{kind}-starter.zip" for kind in ("classification", "world", "arm")})
            for package in packages:
                with zipfile.ZipFile(package) as archive:
                    self.assertIsNone(archive.testzip())
                    self.assertTrue({"README.md", "config.json"}.issubset(archive.namelist()))
                    self.assertFalse(any(name.endswith((".env", ".npy", ".safetensors")) for name in archive.namelist()))
                    if "classification" in package.name:
                        self.assertIn("train.py", archive.namelist())
                        self.assertIn("inference.py", archive.namelist())
                        text = archive.read("README.md").decode()
                        self.assertIn("PyTorch2.8.0+cu128", text)
                        self.assertIn("1,995", text)
                    else:
                        self.assertEqual(len(json.loads(archive.read("practice-scenes.json"))), 3)
                        self.assertIn("practice.py", archive.namelist())

    def test_bundle_contains_tools_and_public_metadata_without_runtime_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            path = build(Path(directory) / "competition.tar.gz")
            with tarfile.open(path) as archive:
                names = archive.getnames()
                self.assertEqual(len(names), len(set(names)))
                self.assertIn("backend/evaluation_adapters/classification_example/train.py", names)
                self.assertIn("backend/evaluation_adapters/classification_runner.py", names)
                self.assertIn("autodl-depth-competition-install.sh", names)
                self.assertIn("depth-class-mapping.csv", names)
                mapping = archive.extractfile("depth-class-mapping.csv").read().decode()
                self.assertEqual(len(mapping.strip().splitlines()), 41)
                expected_mapping = (Path(__file__).resolve().parents[1] /
                    "backend/competitions/wujie-cup-2026/depth-class-mapping.csv").read_bytes()
                self.assertEqual(archive.extractfile("depth-class-mapping.csv").read(), expected_mapping)
                split = json.load(archive.extractfile("depth-subject-split.json"))
                self.assertEqual({k: len(v) for k,v in split.items()}, {"train":12,"validation":3,"test":3})
                for name in names:
                    self.assertNotIn("private", name)
                    self.assertFalse(name.endswith((".env", ".npy", ".safetensors", ".zip")))
                script = archive.extractfile("autodl-depth-competition-install.sh").read()
                self.assertNotIn(b"\r", script)

    def test_training_package_can_be_built_from_epoch_dated_image_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = build(root / "competition.tar.gz")
            with tarfile.open(bundle) as archive:
                for name in archive.getnames():
                    target = root / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.extractfile(name).read())
                    os.utime(target, (0, 0))
            weights = root / "model.safetensors"
            weights.write_bytes(b"checkpoint bytes")
            output = root / "submission.zip"
            with patch.object(build_classification_package, "__file__", str(root / "backend/build_classification_package.py")), \
                 patch.object(sys, "argv", ["package", "--weights", str(weights), "--output", str(output)]), \
                 contextlib.redirect_stdout(io.StringIO()):
                build_classification_package.main()
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(archive.read("model.safetensors"), b"checkpoint bytes")
                self.assertIn("inference.py", archive.namelist())
                self.assertTrue(all(info.date_time[0] >= 1980 for info in archive.infolist()))


if __name__ == "__main__":
    unittest.main()
