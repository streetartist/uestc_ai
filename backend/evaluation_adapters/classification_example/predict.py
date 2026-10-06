"""Generate the public validation CSV from the same inference entry used by judges."""
import argparse
import csv
import json
from pathlib import Path

import numpy as np
import torch
from inference import Model


def predict_dataset(dataset, config_path, output, device):
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["weights_path"] = str((config_path.parent / config["weights_file"]).resolve())
    manifest = json.loads((dataset / "manifest.json").read_text(encoding="utf-8"))
    if len(manifest["classes"]) != 40 or manifest["modality"] != "depth":
        raise ValueError("expected the published 40-class depth dataset")
    model = Model(config, device)
    rows = []
    for index, sample in enumerate(manifest["samples"]):
        path = (dataset / sample["file"]).resolve()
        if not path.is_relative_to(dataset.resolve()):
            raise ValueError("sample path escapes dataset")
        scores = np.asarray(model.predict(str(path)), dtype=float)
        if scores.shape != (40,) or not np.isfinite(scores).all():
            raise ValueError("inference must produce 40 finite scores")
        rows.append((sample["file"], int(scores.argmax())))
        if (index + 1) % 50 == 0 or index + 1 == len(manifest["samples"]):
            print(f"Predicted {index + 1}/{len(manifest['samples'])}", flush=True)
    if len({row[0] for row in rows}) != len(rows):
        raise ValueError("duplicate sample IDs")
    with output.open("w", newline="", encoding="utf-8") as target:
        writer = csv.writer(target)
        writer.writerow(["sample_id", "predicted_class"])
        writer.writerows(rows)
    return len(rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    parser.add_argument("--output", type=Path, default=Path("predictions.csv"))
    args = parser.parse_args()
    predict_dataset(args.dataset, args.config, args.output, "cuda" if torch.cuda.is_available() else "cpu")
