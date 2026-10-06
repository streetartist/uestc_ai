"""Seal installed evaluator code and dependency versions before AutoDL image/save."""
import importlib.metadata
import json
import platform
from pathlib import Path

from evaluation_adapters.classification_runner import digest_file


def main():
    root = Path(__file__).resolve().parent
    manifest = {"environment": {"python": platform.python_version(), **{
        name: importlib.metadata.version(name) for name in ("torch", "torchvision", "numpy", "psutil", "safetensors")}},
        "files": {str(path.relative_to(root)): digest_file(path) for path in sorted(root.rglob("*.py"))}}
    target = root.parent / "runtime-manifest.json"
    target.write_text(json.dumps(manifest, indent=2))
    target.chmod(0o600)
    print(json.dumps(manifest["environment"]))


if __name__ == "__main__": main()
