"""Install a pinned version-2 original depth/IR dataset into a new directory."""
import argparse
import hashlib
import json
import os
import re
import tarfile
from pathlib import Path, PurePosixPath

from evaluation_adapters.depth_ir_dataset import validate_manifest


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def install(archive, output, archive_sha256, manifest_sha256, prefix=''):
    archive, output = Path(archive), Path(output)
    for value in (archive_sha256, manifest_sha256):
        if not re.fullmatch('[a-f0-9]{64}', value):
            raise ValueError('Expected a SHA256 digest')
    if sha256(archive) != archive_sha256:
        raise ValueError('Archive SHA256 mismatch')
    if prefix and (prefix.startswith('/') or '..' in PurePosixPath(prefix).parts or '\\' in prefix):
        raise ValueError('Invalid archive prefix')
    prefix = prefix.strip('/') + '/' if prefix else ''
    temporary = output.with_name(output.name + '.installing')
    if output.exists() or temporary.exists():
        raise FileExistsError('Use a new dataset version; preserve an interrupted installation for inspection')
    temporary.mkdir(parents=True, mode=0o700)
    temporary.chmod(0o700)
    seen, count, total = set(), 0, 0
    with tarfile.open(archive, 'r:*') as source:
        for entry in source:
            name = entry.name
            if name.startswith('/') or '\\' in name or '..' in PurePosixPath(name).parts:
                raise ValueError('Invalid archive path')
            if prefix and not name.startswith(prefix):
                if entry.isdir() and name.rstrip('/') == prefix.rstrip('/'):
                    continue
                raise ValueError('Archive member outside selected prefix')
            relative = name[len(prefix):]
            if entry.isdir():
                continue
            if (not entry.isfile() or relative in seen or not
                    (relative == 'manifest.json' or re.fullmatch(
                        r'[A-Za-z0-9_-]{1,100}/(?:Depth_Color|IR)/[A-Za-z0-9_.-]{1,160}\.(?:png|jpg|jpeg|bmp|tif|tiff)',
                        relative, flags=re.IGNORECASE))):
                raise ValueError('Unexpected, duplicate, or non-regular dataset member')
            count += 1
            total += entry.size
            limit = 16 * 1024**2 if relative == 'manifest.json' else 64 * 1024**2
            if not 0 < entry.size <= limit or count > 500000 or total > 64 * 1024**3:
                raise ValueError('Dataset exceeds installation limits')
            seen.add(relative)
            target = temporary / relative
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            # Existing parents here were created by this installer only.
            for parent in target.parents:
                if parent == temporary.parent:
                    break
                parent.chmod(0o700)
            stream = source.extractfile(entry)
            with target.open('xb') as destination:
                while chunk := stream.read(1024 * 1024):
                    destination.write(chunk)
            target.chmod(0o600)
    manifest = temporary / 'manifest.json'
    if sha256(manifest) != manifest_sha256:
        raise ValueError('Manifest SHA256 mismatch')
    data = json.loads(manifest.read_text(encoding='utf-8'))
    validate_manifest(temporary, data)
    if {p.name for p in temporary.iterdir()} != {'manifest.json', *(s['file'] for s in data['samples'])}:
        raise ValueError('Unexpected dataset root contents')
    temporary.rename(output)
    return {'version': 2, 'modality': 'depth_ir', 'samples': len(data['samples']),
            'files': count, 'bytes': total, 'manifest_sha256': manifest_sha256}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('archive', 'output', 'archive-sha256', 'manifest-sha256'):
        p.add_argument('--' + name, required=True)
    p.add_argument('--prefix', default='')
    args = p.parse_args()
    if not hasattr(os, 'geteuid') or os.geteuid() != 0:
        raise RuntimeError('Install private evaluation data as root on Linux')
    print(json.dumps(install(args.archive, args.output, args.archive_sha256,
                             args.manifest_sha256, args.prefix)))
