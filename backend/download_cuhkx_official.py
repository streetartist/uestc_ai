"""Resumable organizer download from the official gated CUHK-X repository.

Requires a Hugging Face account already granted access. The read token is read
only from HF_TOKEN, never written to progress output or the downloaded metadata.
Use a separate data-preparation environment, outside the sealed evaluator.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
from pathlib import Path

REPOSITORY = "Kevin-Pal/CUHK-X_Small_Model_Track"
MAPPING = "Small-Model-Track/class_mapping.csv"
PREFIX = "Small-Model-Track/Training/data/"


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    from huggingface_hub import HfApi, hf_hub_download
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    token = os.environ.get("HF_TOKEN")
    if not token:
        parser.exit(1, "HF_TOKEN is required; request official dataset access in your browser first.\n")
    output = args.output
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    output.chmod(0o700)
    state_file = output / "download-progress.private.json"
    previous = json.loads(state_file.read_text()) if state_file.exists() else {}
    info = HfApi(token=token).dataset_info(REPOSITORY, revision=previous.get("revision", "main"), files_metadata=True, timeout=30)
    files = []
    for entry in info.siblings:
        if entry.rfilename == MAPPING or (entry.rfilename.startswith(PREFIX)
                and re.fullmatch(r"HAR\.(?:zip|z[0-9]{2})", entry.rfilename[len(PREFIX):])):
            files.append({"path": entry.rfilename, "size": entry.size,
                          "sha256": entry.lfs.get("sha256") if entry.lfs else None})
    if (MAPPING not in {f["path"] for f in files} or PREFIX + "HAR.zip" not in {f["path"] for f in files}
            or not all(type(f["size"]) is int and f["size"] > 0 for f in files)):
        parser.exit(1, "Official repository layout changed; inspect it before downloading.\n")
    state = {"repository": REPOSITORY, "revision": info.sha, "files": files, "completed": previous.get("completed", [])}
    def persist():
        temporary = state_file.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, indent=2))
        temporary.chmod(0o600)
        temporary.replace(state_file)
    persist()
    needed = sum(f["size"] for f in files if not (output / f["path"]).exists())
    if shutil.disk_usage(output).free < needed + 2 * 1024**3:
        parser.exit(1, "Insufficient disk for official archives; expand the preparation disk first.\n")
    # Download the central-directory volume first, to inspect extraction size.
    files.sort(key=lambda f: (f["path"] not in {MAPPING, PREFIX + "HAR.zip"}, f["path"]))
    for index, entry in enumerate(files):
        path = Path(hf_hub_download(REPOSITORY, entry["path"], repo_type="dataset", revision=info.sha,
                                   token=token, local_dir=output, local_dir_use_symlinks=False))
        if path.stat().st_size != entry["size"] or (entry["sha256"] and sha256(path) != entry["sha256"]):
            parser.exit(1, "Official archive size/hash verification failed.\n")
        path.chmod(0o600)
        if entry["path"] not in state["completed"]:
            state["completed"].append(entry["path"])
        persist()
        print(json.dumps({"completed": index + 1, "total": len(files), "file": entry["path"]}), flush=True)
    print(json.dumps({"status": "downloaded", "revision": info.sha, "size": sum(f["size"] for f in files)}), flush=True)


if __name__ == "__main__":
    main()
