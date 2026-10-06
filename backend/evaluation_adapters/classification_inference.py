"""Untrusted inference entry. The parent owns labels, timing and result files."""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path


def main():
    # Drop privileges in a fresh interpreter, avoiding preexec_fn while the
    # trusted worker's heartbeat thread is running.
    path = Path(__file__).with_name("classification_sandbox.py")
    spec = importlib.util.spec_from_file_location("trusted_sandbox", path)
    sandbox = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sandbox)
    sandbox.restrict_process(int(sys.argv[4]), int(sys.argv[5]), int(sys.argv[6]), sys.argv[3])
    # Preserve a dedicated protocol FD so ordinary prints cannot corrupt JSON.
    protocol = os.fdopen(int(sys.argv[2]), "w", encoding="utf-8", buffering=1)
    package = Path(sys.argv[1])
    sys.path.insert(0, str(package))
    spec = importlib.util.spec_from_file_location("contestant_inference", package / "inference.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    config = json.loads((package / "config.json").read_text())
    model = module.Model(config, device="cuda:0")
    if not callable(getattr(model, "predict", None)):
        raise ValueError("Model must implement predict(sample_path)")
    protocol.write('{"type":"ready"}\n')
    for line in sys.stdin:
        message = json.loads(line)
        if message["type"] == "done":
            return
        if message["type"] != "predict":
            raise ValueError("unknown request")
        prediction = model.predict(message["sample_path"])
        # Device completion is included in the trusted wall-clock timing.
        import torch
        torch.cuda.synchronize()
        protocol.write(json.dumps({"type": "prediction", "id": message["id"], "scores": prediction}, allow_nan=False) + "\n")


if __name__ == "__main__":
    main()
