"""Make a small, explicit inference ZIP from trained starter weights."""
import argparse
import zipfile
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--weights", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    if not args.weights.is_file():
        p.error("provide trained safetensors weights")
    example = Path(__file__).with_name("evaluation_adapters") / "classification_example"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, "w", compression=zipfile.ZIP_DEFLATED, strict_timestamps=False) as archive:
        for name in ("inference.py","network.py","train.py","config.json"):
            archive.write(example / name, name)
        archive.write(args.weights, "model.safetensors")
    if args.output.stat().st_size > 20 * 1024**2:
        p.error("package exceeds the platform's 20 MB limit; use config.weights with an approved URL and SHA256")
    print(args.output)


if __name__ == "__main__": main()
