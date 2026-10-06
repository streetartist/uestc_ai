"""Install a verified CUHK-X split; never include test data in participant images."""
import argparse
import json
import re
import shutil
import tarfile
import tempfile
from pathlib import Path

from evaluation_adapters.classification_runner import digest_file, load_dataset

ARCHIVE_SHA256 = "3a022b03a06078e2695cc5fcbf39acfc47691fd1c03d0034d97bba04d4281521"
TEST_MANIFEST_SHA256 = "32b5e9e0f09b578757dcbc3ba763020d558bfdbce7fbaad6151d936a6a10b303"
MANIFEST_SHA256_BY_SPLIT = {
    "train": "b1da519efcaffea98e2b153f39a3195bfa902617826135e866bbca289380ed28",
    "validation": "6e5c15ff25b726d4925c4e58820204746db83551e10156a303571c0d84b6baa5",
    "test": TEST_MANIFEST_SHA256,
}


def install(archive, output, archive_sha256=ARCHIVE_SHA256, manifest_sha256=None, source_split="test"):
    archive, output = Path(archive), Path(output)
    if source_split not in MANIFEST_SHA256_BY_SPLIT:
        raise ValueError("select train, validation or test")
    manifest_sha256 = manifest_sha256 or MANIFEST_SHA256_BY_SPLIT[source_split]
    source_prefix = "uestc-depth-prepared-v1/" + source_split + "/"
    for digest in (archive_sha256, manifest_sha256):
        if not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError("expected SHA256 must be 64 lowercase hex characters")
    if digest_file(archive) != archive_sha256:
        raise ValueError("prepared data archive failed SHA256 verification")
    if output.is_symlink():
        raise ValueError("private dataset output cannot be a symlink")
    if output.exists():
        data = load_dataset(output, manifest_sha256)
        return {"dataset": output.name, "samples": len(data["samples"]), "manifest_sha256": manifest_sha256}
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    output.parent.chmod(0o700)
    with tempfile.TemporaryDirectory(prefix=".classification-install-", dir=output.parent) as directory:
        staging = Path(directory) / "dataset"
        staging.mkdir(mode=0o700)
        staging.chmod(0o700)
        seen = set()
        with tarfile.open(archive, "r|gz") as stream:
            for member in stream:
                if not member.name.startswith(source_prefix):
                    continue
                name = member.name[len(source_prefix):]
                if not name and member.isdir():
                    continue
                if (not member.isfile() or name in seen or len(seen) >= 100001
                        or not re.fullmatch(r"manifest\.json|[a-zA-Z0-9_-]{1,100}\.npy", name)
                        or member.size > 64*1024**2):
                    raise ValueError("unexpected entry in the private test archive")
                seen.add(name)
                with stream.extractfile(member) as source, (staging / name).open("wb") as destination:
                    shutil.copyfileobj(source, destination, 1024*1024)
                (staging / name).chmod(0o600)
        data = load_dataset(staging, manifest_sha256)
        if seen != {"manifest.json", *(sample["file"] for sample in data["samples"])}:
            raise ValueError("test archive contains undeclared files")
        staging.rename(output)
    return {"dataset": output.name, "samples": len(data["samples"]), "manifest_sha256": manifest_sha256}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--split", choices=tuple(MANIFEST_SHA256_BY_SPLIT), default="test")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or (Path("/opt/uestc-classification/datasets/wujie-depth-v1") if args.split == "test"
                            else Path("/opt/uestc-classification/training-data") / args.split)
    print(json.dumps(install(args.archive, output, source_split=args.split)))


if __name__ == "__main__":
    main()
