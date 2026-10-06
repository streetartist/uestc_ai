"""Package only reviewed code and public CUHK-X metadata for AutoDL installation."""
import argparse
import io
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build(output):
    names = ("classification_worker.py", "evaluation_worker.py", "evaluation_evidence.py", "classification_smoke_test.py",
             "build_classification_image_manifest.py", "build_classification_package.py",
             "prepare_cuhkx_depth.py", "download_cuhkx_official.py", "install_classification_image_data.py")
    entries = [(ROOT / "backend" / name, "backend/" + name) for name in names]
    adapters = ROOT / "backend/evaluation_adapters"
    for name in ("__init__.py", "classification_assets.py", "classification_inference.py",
                 "classification_runner.py", "classification_sandbox.py"):
        entries.append((adapters / name, "backend/evaluation_adapters/" + name))
    for name in ("train.py", "network.py", "inference.py", "config.json", "predict.py"):
        entries.append((adapters / "classification_example" / name,
                        "backend/evaluation_adapters/classification_example/" + name))
    for name in ("autodl-classification-install.sh", "autodl-depth-competition-install.sh",
                 "autodl-classification-start.sh", "autodl-classification.env.example"):
        entries.append((ROOT / "deploy/linux" / name, name))
    entries.append((ROOT / "deploy/linux/autodl-depth-competition.README.md", "README.md"))
    for name in ("autodl-depth-participant.README.md", "autodl-depth-organizer.README.md"):
        entries.append((ROOT / "deploy/linux" / name, name))
    for name in ("depth-class-mapping.csv", "depth-subject-split.json"):
        entries.append((ROOT / "backend/competitions/wujie-cup-2026" / name, name))
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output, "w:gz") as archive:
        for path, name in entries:
            data = path.read_bytes()
            if path.suffix != ".csv":
                data = data.replace(b"\r\n", b"\n")
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), 0o644
            # A fixed 1980 date is reproducible and usable by downstream ZIP tools.
            info.mtime = 315619200
            archive.addfile(info, io.BytesIO(data))
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(build(args.output))


if __name__ == "__main__":
    main()
