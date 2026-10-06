"""Repair hfd's scientific-notation byte counters without deleting downloads."""
import argparse
from pathlib import Path


def fix(script, directory):
    text = script.read_text(encoding="utf-8")
    replacements = [
        ('END { print (n+0)" "(s+0) }', 'END { printf "%.0f %.0f\\n", n+0, s+0 }'),
        ('END { print b*512, d-a }', 'END { printf "%.0f %.0f\\n", b*512, d-a }'),
    ]
    for old, new in replacements:
        if old not in text and new not in text:
            raise ValueError("hfd version changed; inspect the progress counters before patching")
        text = text.replace(old, new)
    backup = script.with_suffix(".sh.before-integer-fix")
    if not backup.exists():
        backup.write_bytes(script.read_bytes())
        backup.chmod(0o600)
    script.write_bytes(text.encode("utf-8"))
    meta = directory / ".hfd"
    manifest = meta / "manifest"
    if manifest.exists():
        files = {}
        for line in manifest.read_text(encoding="utf-8").splitlines():
            size, name = line.split("\t", 1)
            files[name] = int(size)
        count, total = len(files), sum(files.values())
        for name in ("filelist_stats", "repo_info"):
            path = meta / name
            path.write_bytes(f"{count} {total}\n".encode("utf-8"))
            path.chmod(0o600)
        return count, total
    return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--script", type=Path, default=Path("/root/hfd.sh"))
    parser.add_argument("--directory", type=Path, default=Path("/root/autodl-tmp/cuhkx-source"))
    args = parser.parse_args()
    result = fix(args.script, args.directory)
    print("hfd integer counters repaired", result)
