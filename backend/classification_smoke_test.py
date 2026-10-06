"""Acceptance data is SYNTHETIC, and cannot be used as Wujie competition scores."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import zipfile
from pathlib import Path

import numpy as np

from evaluation_adapters.classification_runner import digest_file, evaluate


EXAMPLE = '''import numpy as np
import torch
class Model:
    def __init__(self, config, device):
        self.device = device
    def predict(self, sample_path):
        # Synthetic acceptance encodes a class in a depth value. This is NOT
        # a trained activity-recognition baseline and must never be ranked.
        x = torch.tensor(np.load(sample_path, allow_pickle=False), device=self.device)
        index = int(x.mean().round().item())
        return [float(i == index) for i in range(40)]
'''


def fixtures(root):
    data = root / "synthetic-40"
    data.mkdir(mode=0o700, exist_ok=True)
    data.chmod(0o700)
    samples = []
    for index in range(40):
        path = data / f"sample-{index:02}.npy"
        np.save(path, np.full((4, 1, 16, 16), index, dtype=np.float32))
        samples.append({"file": path.name, "label": index, "sha256": digest_file(path)})
    manifest = {"version": 1, "modality": "depth", "classes": [f"synthetic-{i:02}" for i in range(40)], "samples": samples}
    (data / "manifest.json").write_text(json.dumps(manifest))
    return data, digest_file(data / "manifest.json")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--gpu", required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True, mode=0o700)
    data, digest = fixtures(args.output)
    tests = {}
    sources = {
        "positive": EXAMPLE,
        "wrong": EXAMPLE.replace("int(x.mean().round().item())", "(int(x.mean().round().item()) + 1) % 40"),
        "isolation": EXAMPLE.replace("self.device = device", '''self.device = device
        import os, socket
        assert "EVALUATION_WORKER_TOKEN" not in os.environ
        for path in ("/root/autodl-tmp/uestc-evaluation", "''' + str(data) + '''/manifest.json"):
            try:
                open(path).read()
            except (PermissionError, IsADirectoryError):
                pass
            else:
                raise RuntimeError("private label isolation failed")
        try:
            socket.socket()
        except PermissionError:
            pass
        else:
            raise RuntimeError("network isolation failed")
        try:
            os.setsid()
        except PermissionError:
            pass
        else:
            raise RuntimeError("process group isolation failed")'''),
        "invalid": EXAMPLE.replace("[float(i == index) for i in range(40)]", "[float('nan')] * 40"),
        "timeout": EXAMPLE.replace("x = torch.tensor", "import time; time.sleep(120)\n        x = torch.tensor"),
        "forged": EXAMPLE.replace("x = torch.tensor", '''from pathlib import Path
        # A contestant cannot write the trusted result directory.
        try:
            Path("''' + str(args.output / 'forged' / 'result.json') + '''").write_text('{"episodes":[{"accuracy":100}]}')
        except PermissionError:
            pass
        else:
            raise RuntimeError("trusted result isolation failed")
        x = torch.tensor'''),
    }
    for name, source in sources.items():
        package = args.output / (name + ".zip")
        with zipfile.ZipFile(package, "w") as archive:
            archive.writestr("inference.py", source)
            archive.writestr("config.json", '{"num_classes":40}')
        try:
            metric = evaluate(package, data, digest, args.output / name, gpu=args.gpu, seconds=8 if name == "timeout" else 90)
        except (RuntimeError, ValueError, TimeoutError) as error:
            if name not in {"invalid", "timeout"}:
                raise
            tests[name] = {"status": "rejected", "error": str(error)}
        else:
            if name in {"invalid", "timeout"}:
                raise AssertionError("malformed or slow inference must fail")
            assert metric["accuracy"] == (0 if name == "wrong" else 100), metric
            assert metric["macro_f1"] == metric["accuracy"], metric
            assert metric["peak_vram_mb"] > 0 and metric["latency_ms"] > 0
            tests[name] = {"status": "completed", "metrics": metric}
        print(name, tests[name]["status"], flush=True)
    result = {"synthetic_only": True, "formal_dataset_installed": False, "tests": tests}
    (args.output / "verification.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
