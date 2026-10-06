import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from prepare_cuhkx_depth import convert_clip, load_classes, load_split, prepare
from evaluation_adapters.classification_runner import digest_file


class CUHKXPreparationTests(unittest.TestCase):
    def fixture(self, root):
        source = root / "Depth_Color"
        mapping, split = root / "classes.csv", root / "split.json"
        with mapping.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(["action_id", "action_name"])
            for label in range(40):
                writer.writerow([label, f"Action_{label}"])
                for subject in range(1, 4):
                    trial = source / f"{label}_Action_{label}" / f"user{subject}" / "1-1-1"
                    trial.mkdir(parents=True)
                    Image.new("RGB", (4, 4), (label * 3 + subject,) * 3).save(trial / "Depth_time_2_Color.png")
        split.write_text(json.dumps({"train": ["user1"], "validation": ["user2"], "test": ["user3"]}))
        return source, mapping, split

    def test_preparation_separates_subjects_and_seals_test_labels(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, mapping, split = self.fixture(root)
            report = prepare(source, mapping, split)
            self.assertEqual(report["counts_per_class"]["test"], [1] * 40)
            self.assertFalse((root / "dataset").exists())
            result = prepare(source, mapping, split, root / "dataset")
            for group in ("train", "validation", "test"):
                manifest = root / "dataset" / group / "manifest.json"
                self.assertEqual(result["manifest_sha256"][group], digest_file(manifest))
                samples = json.loads(manifest.read_text())["samples"]
                self.assertEqual(len(samples), 40)
                self.assertEqual(set(s["label"] for s in samples), set(range(40)))
                array = np.load(manifest.parent / samples[0]["file"], allow_pickle=False)
                self.assertEqual(array.shape, (16, 1, 112, 112))
                self.assertEqual(array.dtype, np.float32)
            with self.assertRaises(ValueError): prepare(source, mapping, split, root / "dataset")

    def test_split_overlap_and_incomplete_class_coverage_fail_before_conversion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, mapping, split = self.fixture(root)
            split.write_text(json.dumps({"train": ["user1"], "validation": ["user2"], "test": ["user1"]}))
            with self.assertRaises(ValueError): load_split(split)
            split.write_text(json.dumps({"train": ["user1"], "validation": ["user2"], "test": ["user3"]}))
            missing = source / "39_Action_39" / "user3" / "1-1-1" / "Depth_time_2_Color.png"
            missing.unlink()
            with self.assertRaises(ValueError): prepare(source, mapping, split, root / "dataset")
            self.assertFalse((root / "dataset.incomplete").exists())

    def test_numeric_frame_order_and_luminance_protocol(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, mapping, split = self.fixture(root)
            trial = source / "0_Action_0" / "user1" / "1-1-1"
            Image.new("RGB", (4, 4), (200, 200, 200)).save(trial / "Depth_time_10_Color.png")
            from prepare_cuhkx_depth import discover
            clips, _ = discover(source, load_classes(mapping), load_split(split))
            clip = next(c for c in clips if c[2] == "0_Action_0/user1/1-1-1")
            array = convert_clip(clip[3])
            self.assertAlmostEqual(float(array[0, 0, 0, 0]), 1 / 255)
            self.assertAlmostEqual(float(array[-1, 0, 0, 0]), 200 / 255)

    def test_duplicate_clip_cannot_cross_splits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, mapping, split = self.fixture(root)
            frame = source / "0_Action_0" / "user3" / "1-1-1" / "Depth_time_2_Color.png"
            Image.new("RGB", (4, 4), (1, 1, 1)).save(frame)
            with self.assertRaisesRegex(ValueError, "cross dataset splits"):
                prepare(source, mapping, split, root / "dataset")
            self.assertFalse((root / "dataset").exists())

    def test_official_frames_without_timestamp_are_accepted_and_sorted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, mapping, split = self.fixture(root)
            trial = source / "0_Action_0" / "user1" / "1-1-1"
            (trial / "Depth_time_2_Color.png").rename(trial / "Depth_00000002_Color.png")
            Image.new("RGB", (4, 4), (200, 200, 200)).save(
                trial / "Depth_2025-05-07_11-49-24.416_00000010_Color.png")
            from prepare_cuhkx_depth import discover
            clips, _ = discover(source, load_classes(mapping), load_split(split))
            clip = next(c for c in clips if c[2] == "0_Action_0/user1/1-1-1")
            array = convert_clip(clip[3])
            self.assertAlmostEqual(float(array[0, 0, 0, 0]), 1 / 255)
            self.assertAlmostEqual(float(array[-1, 0, 0, 0]), 200 / 255)


if __name__ == "__main__": unittest.main()
