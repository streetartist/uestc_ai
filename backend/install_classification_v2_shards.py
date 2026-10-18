"""Download and install a selected public depth/IR split from a pinned release index."""
import argparse
import hashlib
import json
import re
import stat
import sys
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from evaluation_adapters.depth_ir_dataset import validate_manifest
from install_classification_v2_data import sha256


def validate_index(data):
    if (not isinstance(data, dict) or data.get('version') != 2 or data.get('modality') != 'depth_ir'
            or not isinstance(data.get('archives'), list) or not data['archives']
            or not isinstance(data.get('splits'), dict) or set(data['splits']) != {'train', 'validation'}):
        raise ValueError('Expected a version-2 public depth_ir release index')
    names = set()
    for item in data['archives']:
        if (not isinstance(item, dict) or set(item) != {'file', 'bytes', 'sha256'}
                or not isinstance(item['file'], str)
                or not re.fullmatch(r'(train|validation)-[0-9]{3}\.zip', item['file'])
                or item['file'] in names or type(item['bytes']) is not int
                or not 0 < item['bytes'] <= 2 * 1024**3
                or not isinstance(item['sha256'], str) or not re.fullmatch('[a-f0-9]{64}', item['sha256'])):
            raise ValueError('Invalid shard in release index')
        names.add(item['file'])
    for split, info in data['splits'].items():
        if (not isinstance(info, dict) or set(info) != {'count', 'manifest_sha256'}
                or type(info['count']) is not int or not 1 <= info['count'] <= 100000
                or not isinstance(info['manifest_sha256'], str)
                or not re.fullmatch('[a-f0-9]{64}', info['manifest_sha256'])
                or not any(name.startswith(split + '-') for name in names)):
            raise ValueError('Invalid split metadata')
    return data


def load_index(path, expected_sha256):
    if not re.fullmatch('[a-f0-9]{64}', expected_sha256) or sha256(path) != expected_sha256:
        raise ValueError('Release index SHA256 mismatch')
    return validate_index(json.loads(Path(path).read_text(encoding='utf-8')))


def download_shard(base_url, item, cache):
    """Completed cached shards are reused; incomplete downloads never look complete."""
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme != 'https' or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Download base URL must be an HTTPS directory')
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / item['file']
    if target.exists():
        if target.is_symlink() or target.stat().st_size != item['bytes'] or sha256(target) != item['sha256']:
            raise ValueError('Cached shard does not match release index')
        return target
    partial = target.with_suffix('.partial')
    if partial.is_symlink():
        raise ValueError('Download target must be a regular file')
    digest, size = hashlib.sha256(), 0
    url = base_url.rstrip('/') + '/' + item['file']
    with urllib.request.urlopen(url, timeout=60) as response:
        if urllib.parse.urlparse(response.url).scheme != 'https':
            raise ValueError('Download redirect must use HTTPS')
        with partial.open('wb') as output:
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > item['bytes']:
                    raise ValueError('Download exceeds published shard size')
                digest.update(chunk)
                output.write(chunk)
    if size != item['bytes'] or digest.hexdigest() != item['sha256']:
        raise ValueError('Downloaded shard SHA256/size mismatch')
    partial.replace(target)
    return target


def install_shards(index, archives, output, split, progress=None):
    """Install only the requested split, preserving a failed staging directory for diagnosis."""
    validate_index(index)
    if split not in ('train', 'validation'):
        raise ValueError('Select train or validation')
    archives, output = Path(archives), Path(output)
    selected = sorted((s for s in index['archives'] if s['file'].startswith(split + '-')), key=lambda s: s['file'])
    for number, item in enumerate(selected, 1):
        source = archives / item['file']
        if source.is_symlink() or source.stat().st_size != item['bytes'] or sha256(source) != item['sha256']:
            raise ValueError('Shard SHA256/size mismatch')
        if progress:
            progress('verified archives', number, len(selected))
    temporary = output.with_name(output.name + '.installing')
    if output.exists() or temporary.exists():
        raise FileExistsError('Use a new dataset version directory')
    temporary.mkdir(parents=True, mode=0o700)
    temporary.chmod(0o700)
    seen, total = set(), 0
    pattern = re.compile(r'[A-Za-z0-9_-]{1,100}/(?:Depth_Color|IR)/[A-Za-z0-9_.-]{1,160}\.png')
    for number, item in enumerate(selected, 1):
        with zipfile.ZipFile(archives / item['file']) as archive:
            for entry in archive.infolist():
                name = entry.filename
                if not name.startswith(split + '/') or '\\' in name or '..' in name.split('/'):
                    raise ValueError('Shard member outside selected split')
                relative = name[len(split) + 1:]
                if (entry.is_dir() or stat.S_IFMT(entry.external_attr >> 16) not in (0, stat.S_IFREG)
                        or relative in seen or not (relative in ('manifest.json', 'provenance.json') or pattern.fullmatch(relative))):
                    raise ValueError('Unexpected, duplicate, or non-regular shard member')
                total += entry.file_size
                if not 0 < entry.file_size <= 16 * 1024**2 or total > 16 * 1024**3 or len(seen) >= 500000:
                    raise ValueError('Public dataset exceeds installation limits')
                seen.add(relative)
                target = temporary / relative
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with archive.open(entry) as source, target.open('xb') as destination:
                    while chunk := source.read(1024 * 1024):
                        destination.write(chunk)
                target.chmod(0o600)
        if progress:
            progress('extracted archives', number, len(selected))
    manifest = temporary / 'manifest.json'
    if sha256(manifest) != index['splits'][split]['manifest_sha256']:
        raise ValueError('Split manifest SHA256 mismatch')
    def sample_progress(done, count):
        if progress and (done == 1 or done % 100 == 0 or done == count):
            progress('verified clips', done, count)
    data = validate_manifest(temporary, json.loads(manifest.read_text(encoding='utf-8')), on_sample=sample_progress)
    if len(data['samples']) != index['splits'][split]['count']:
        raise ValueError('Split sample count mismatch')
    if {p.name for p in temporary.iterdir()} != {'manifest.json', 'provenance.json', *(s['file'] for s in data['samples'])}:
        raise ValueError('Unexpected split root contents')
    temporary.rename(output)
    return {'version': 2, 'split': split, 'samples': len(data['samples']), 'bytes': total,
            'manifest_sha256': index['splits'][split]['manifest_sha256']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index', type=Path, required=True)
    parser.add_argument('--index-sha256', required=True)
    parser.add_argument('--archives', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--split', choices=('train', 'validation'), required=True)
    parser.add_argument('--download-base-url', help='Optional HTTPS directory containing the listed ZIP shards')
    args = parser.parse_args()
    index = load_index(args.index, args.index_sha256)
    if args.download_base_url:
        for item in index['archives']:
            if item['file'].startswith(args.split + '-'):
                download_shard(args.download_base_url, item, args.archives)
    def progress(stage, done, count):
        print(f'{args.split}: {stage} {done}/{count}', file=sys.stderr, flush=True)
    print(json.dumps(install_shards(index, args.archives, args.output, args.split, progress=progress)))


if __name__ == '__main__':
    main()
