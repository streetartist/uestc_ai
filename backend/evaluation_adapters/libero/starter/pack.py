"""Build the exact ZIP used by local practice and website submissions."""
import argparse
import json
import zipfile
from pathlib import Path


def build(root, output):
    files = json.loads((root / "submission-files.json").read_text(encoding="utf-8"))
    selected = []
    for name in files:
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            raise ValueError(f"Submission file not found in the project: {name}")
        if path.suffix.lower() in (".key", ".pem") or path.name.startswith(".env"):
            raise ValueError(f"Remove credentials from submission-files.json: {name}")
        selected.append((path, name))
    for required in ("agent.py", "config.json"):
        if required not in files:
            raise ValueError(f"submission-files.json must include {required}")
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, name in selected:
            archive.write(path, name)
    if output.stat().st_size > 20 * 1024 * 1024:
        raise ValueError("Submission exceeds the website's 20 MiB limit")
    print(f"Submission: {output} ({output.stat().st_size} bytes)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("agent.zip"))
    args = parser.parse_args()
    build(Path(__file__).resolve().parent, args.output)
