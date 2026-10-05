"""Populate ForgeGradle's Minecraft asset cache from the official HTTPS host."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import sys
import time
import urllib.request


def prepare_asset(entry, directory):
    digest, size = entry["hash"], entry["size"]
    if not re.fullmatch(r"[0-9a-f]{40}", digest) or type(size) is not int or size < 0:
        raise ValueError("Invalid Minecraft asset index entry")
    target = directory / "objects" / digest[:2] / digest
    if target.is_file():
        data = target.read_bytes()
        if len(data) == size and hashlib.sha1(data).hexdigest() == digest:
            return
    for attempt in range(3):
        try:
            with urllib.request.urlopen(
                f"https://resources.download.minecraft.net/{digest[:2]}/{digest}", timeout=60
            ) as response:
                data = response.read(size + 1)
            if len(data) != size or hashlib.sha1(data).hexdigest() != digest:
                raise ValueError(f"Minecraft asset checksum mismatch: {digest}")
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(".download")
            temporary.write_bytes(data)
            temporary.replace(target)
            return
        except OSError:
            if attempt == 2:
                raise
            time.sleep(attempt + 1)


def main():
    index, directory = Path(sys.argv[1]), Path(sys.argv[2])
    entries = {entry["hash"]: entry for entry in json.loads(index.read_text())["objects"].values()}
    print(f"Verifying {len(entries)} Minecraft assets (official HTTPS host)", flush=True)
    with ThreadPoolExecutor(max_workers=8) as pool:
        for count, _ in enumerate(pool.map(lambda item: prepare_asset(item, directory), entries.values()), 1):
            if count % 100 == 0 or count == len(entries):
                print(f"Assets verified: {count}/{len(entries)}", flush=True)


if __name__ == "__main__":
    main()
