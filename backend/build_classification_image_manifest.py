"""Seal installed evaluator code and dependency versions before AutoDL image/save."""
import importlib.metadata
import json
import platform
import argparse
from pathlib import Path

from evaluation_adapters.classification_runner import digest_file


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=('depth-v1', 'depth-ir-v2'), default='depth-v1')
    args = parser.parse_args()
    dependencies = ['torch', 'torchvision', 'numpy', 'psutil', 'safetensors']
    if args.profile == 'depth-ir-v2':
        dependencies += ['Pillow', 'ultralytics', 'ultralytics-thop', 'opencv-python-headless', 'timm']
    root = Path(__file__).resolve().parent
    manifest = {"environment": {"python": platform.python_version(), **{
        name: importlib.metadata.version(name) for name in dependencies}},
        "files": {str(path.relative_to(root)): digest_file(path) for path in sorted(root.rglob("*.py"))}}
    target = root.parent / "runtime-manifest.json"
    target.write_text(json.dumps(manifest, indent=2))
    target.chmod(0o600)
    print(json.dumps(manifest["environment"]))


if __name__ == "__main__": main()
