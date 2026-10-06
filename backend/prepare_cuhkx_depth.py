"""Convert an officially obtained CUHK-X Depth_Color release to a fixed protocol.

No download or publication is performed. Subjects are split before conversion;
test labels and source identities remain in the organizer-only output directory.
Colorized depth is converted to luminance, never interpreted as metric depth.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from pathlib import Path

import numpy as np
from PIL import Image, __version__ as pillow_version

from download_cuhkx_official import sha256 as digest_file

PREPROCESSING = {"version": "cuhkx-colorized-depth-luma-v1", "source": "Depth_Color",
                 "frames": 16, "height": 112, "width": 112, "layout": "T,1,H,W",
                 "dtype": "float32", "range": [0, 1], "temporal": "uniform-endpoints-floor",
                 "spatial": "Pillow-bilinear", "color": "Pillow-L-luminance-not-metric-depth"}


def load_classes(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not {"action_id", "action_name"}.issubset(reader.fieldnames or []):
            raise ValueError("official mapping requires action_id and action_name")
        mapping = {}
        for row in reader:
            label, name = int(row["action_id"]), row["action_name"].strip()
            if label in mapping or not 0 <= label < 40 or not name:
                raise ValueError("invalid or duplicate official class mapping")
            mapping[label] = name
    if set(mapping) != set(range(40)) or len(set(mapping.values())) != 40:
        raise ValueError("official mapping must cover exactly 40 unique classes")
    return [mapping[i] for i in range(40)]


def load_split(path):
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if set(data) != {"train", "validation", "test"}:
        raise ValueError("split requires train, validation and test subject lists")
    seen = set()
    for subjects in data.values():
        if not isinstance(subjects, list) or not subjects:
            raise ValueError("every subject split must be nonempty")
        for subject in subjects:
            if (not isinstance(subject, str) or not re.fullmatch(r"user[1-9][0-9]*", subject)
                    or subject in seen):
                raise ValueError("subjects cannot overlap or repeat across splits")
            seen.add(subject)
    return data


def discover(source, classes, split):
    source = Path(source)
    if source.is_symlink() or not source.is_dir() or source.name != "Depth_Color":
        raise ValueError("source must be the extracted official Depth_Color directory")
    assignments = {subject: group for group, subjects in split.items() for subject in subjects}
    clips = []
    coverage = {group: [0] * 40 for group in split}
    subjects_seen = set()
    for action in sorted(source.iterdir()):
        if action.is_symlink() or not action.is_dir():
            raise ValueError("unexpected entry in the modality directory")
        match = re.fullmatch(r"([0-9]+)_(.+)", action.name)
        if not match:
            raise ValueError("action directory needs its official numeric label")
        label = int(match[1])
        if not 0 <= label < 40 or classes[label] not in {action.name, match[2]}:
            raise ValueError("action directory disagrees with official class mapping")
        for subject in sorted(action.iterdir()):
            if subject.is_symlink() or not subject.is_dir():
                raise ValueError("unexpected subject entry")
            if subject.name not in assignments:
                continue
            group = assignments[subject.name]
            for trial in sorted(subject.iterdir()):
                if trial.is_symlink() or not trial.is_dir():
                    raise ValueError("unexpected trial entry")
                indexed = {}
                for frame in trial.iterdir():
                    # Both timestamped names and Depth_00000205_Color.png occur
                    # in the official release; the final numeric index is time order.
                    match = re.fullmatch(r"Depth_(?:.+_)?([0-9]+)_Color\.png", frame.name)
                    if frame.is_symlink() or not frame.is_file() or not match:
                        raise ValueError("only official colorized-depth PNG frames are accepted")
                    index = int(match[1])
                    if index in indexed:
                        raise ValueError("duplicate frame index in a clip")
                    indexed[index] = frame
                if not indexed:
                    raise ValueError("empty depth clip")
                clips.append((group, label, trial.relative_to(source).as_posix(),
                              [indexed[i] for i in sorted(indexed)]))
                coverage[group][label] += 1
                subjects_seen.add(subject.name)
    if subjects_seen != set(assignments):
        raise ValueError("configured subjects are absent from this release")
    for group, counts in coverage.items():
        missing = [i for i, count in enumerate(counts) if not count]
        if missing:
            raise ValueError(f"{group} lacks classes {missing}; revise the split before publication")
    return clips, coverage


def convert_clip(frames):
    indices = np.linspace(0, len(frames) - 1, PREPROCESSING["frames"]).astype(int)
    arrays = []
    for index in indices:
        with Image.open(frames[index]) as image:
            if image.format != "PNG" or image.width > 4096 or image.height > 4096:
                raise ValueError("unexpected frame format or dimensions")
            image = image.convert("L").resize((112, 112), Image.Resampling.BILINEAR)
            arrays.append(np.asarray(image, dtype=np.float32) / 255)
    return np.stack(arrays)[:, None]


def prepare(source, mapping, split_file, output=None, progress=None):
    classes, split = load_classes(mapping), load_split(split_file)
    clips, coverage = discover(source, classes, split)
    report = {"classes": classes, "subjects": split, "counts_per_class": coverage,
              "preprocessing": PREPROCESSING, "source_mapping_sha256": digest_file(mapping),
              "source_split_sha256": digest_file(split_file), "public_source": True,
              "tool_versions": {"Pillow": pillow_version, "numpy": np.__version__}}
    if output is None:
        return report
    output = Path(output)
    if output.exists():
        raise ValueError("output must be a new versioned directory")
    staging = output.with_name(output.name + ".incomplete")
    staging.mkdir(parents=True, mode=0o700)
    staging.chmod(0o700)
    groups = {name: staging / name for name in split}
    for directory in groups.values():
        directory.mkdir(mode=0o700)
        directory.chmod(0o700)
    samples = {name: [] for name in split}
    source_audit, digests = [], {}
    if progress:
        progress(0, len(clips))
    for completed, (group, label, relative, frames) in enumerate(clips, 1):
        array = convert_clip(frames)
        content = hashlib.sha256(array.tobytes()).hexdigest()
        if content in digests and digests[content] != group:
            raise ValueError("identical converted clips cross dataset splits")
        digests[content] = group
        name = hashlib.sha256(relative.encode()).hexdigest()[:32] + ".npy"
        target = groups[group] / name
        np.save(target, array, allow_pickle=False)
        target.chmod(0o600)
        samples[group].append({"file": name, "label": label, "sha256": digest_file(target)})
        source_audit.append({"group": group, "file": name, "source": relative,
                             "frames": [{"file": frame.name, "sha256": digest_file(frame)} for frame in frames]})
        if progress and (completed % 50 == 0 or completed == len(clips)):
            progress(completed, len(clips))
    hashes = {}
    for group, directory in groups.items():
        manifest = directory / "manifest.json"
        manifest.write_text(json.dumps({"version": 1, "modality": "depth", "classes": classes,
                                       "samples": samples[group]}, ensure_ascii=False), encoding="utf-8")
        manifest.chmod(0o600)
        hashes[group] = digest_file(manifest)
    for name, data in (("preparation.json", report), ("source-audit.private.json", source_audit)):
        target = staging / name
        target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        target.chmod(0o600)
    staging.rename(output)
    return {**report, "manifest_sha256": hashes, "output": str(output)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="HAR/data/Depth_Color")
    parser.add_argument("--mapping", type=Path, required=True, help="Official class_mapping.csv")
    parser.add_argument("--split", type=Path, required=True, help="Explicit cross-subject split JSON")
    parser.add_argument("--output", type=Path, help="Omit to inspect mapping and coverage without writing clips")
    parser.add_argument("--progress", action="store_true", help="Write conversion progress to stderr")
    args = parser.parse_args()
    try:
        progress = (lambda completed, total: print(json.dumps({"converted": completed, "total": total}),
                                                   file=sys.stderr, flush=True)) if args.progress else None
        print(json.dumps(prepare(args.source, args.mapping, args.split, args.output, progress=progress), ensure_ascii=False))
    except (ValueError, OSError) as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
