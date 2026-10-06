"""Stage organizer-provided depth NPY clips and hidden labels on AutoDL."""
from __future__ import annotations

import argparse
import csv
import json
import shutil
from pathlib import Path

from evaluation_adapters.classification_runner import digest_file, load_dataset


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--clips", type=Path, required=True)
    p.add_argument("--labels", type=Path, required=True, help="Private CSV with exactly file,label columns; labels 0..39")
    p.add_argument("--classes", type=Path, required=True, help="Ordered JSON array of 40 official class names")
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    if args.output.exists():
        p.error("dataset already exists; use a new versioned dataset directory")
    args.output.mkdir(parents=True, mode=0o700)
    args.output.chmod(0o700)
    samples = []
    with args.labels.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != ["file", "label"]:
            p.error("labels CSV needs exactly file,label columns")
        for row in reader:
            name = row["file"]
            if Path(name).name != name or not name.endswith(".npy") or "\\" in name:
                p.error("only plain depth NPY filenames are accepted")
            source = args.clips / name
            if source.is_symlink() or not source.is_file():
                p.error("sample must be a regular NPY file")
            target = args.output / name
            if target.exists():
                p.error("duplicate sample filename")
            shutil.copyfile(source, target)
            target.chmod(0o600)
            samples.append({"file":name,"label":int(row["label"]),"sha256":digest_file(target)})
    manifest = args.output / "manifest.json"
    manifest.write_text(json.dumps({"version":1,"modality":"depth","classes":json.loads(args.classes.read_text()),"samples":samples}))
    manifest.chmod(0o600)
    digest = digest_file(manifest)
    load_dataset(args.output, digest)
    print(json.dumps([{"dataset":args.output.name,"manifest_sha256":digest}]))


if __name__ == "__main__": main()
