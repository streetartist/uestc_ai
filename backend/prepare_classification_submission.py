"""Build a small inference ZIP with a private uploaded-weight reference."""
import argparse
import json
from pathlib import Path, PurePosixPath
import tempfile
import zipfile

from check_classification_submission import check


def build(source, reference, output, include=()):
    source, output = Path(source).resolve(), Path(output)
    config = json.loads((source / 'config.json').read_text(encoding='utf-8'))
    reference = json.loads(Path(reference).read_text(encoding='utf-8'))
    if set(reference) != {'weights_asset'}:
        raise ValueError('reference must contain weights_asset from the uploader')
    for key in ('weights', 'weights_file', 'weights_path', 'weights_asset'):
        config.pop(key, None)
    config.update(reference)
    files = {p.name for p in source.glob('*.py')} | set(include)
    if config.get('detector_file'):
        files.add(config['detector_file'])
    paths = {}
    for name in sorted(files):
        relative = PurePosixPath(name)
        path = source.joinpath(*relative.parts)
        if (relative.is_absolute() or '..' in relative.parts or '\\' in name or path.is_symlink()
                or source not in path.resolve().parents or not path.is_file() or name == 'config.json'):
            raise ValueError('include files must be regular files inside the source directory')
        paths[name] = path
    if 'inference.py' not in paths:
        raise ValueError('source must contain inference.py')
    if len(paths) > 254 or sum(p.stat().st_size for p in paths.values()) > 19 * 1024 * 1024:
        raise ValueError('keep the inference code and detector below 19 MiB; upload classifier weights separately')
    if output.exists():
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='classification-package-') as temporary:
        candidate = Path(temporary) / 'inference.zip'
        with zipfile.ZipFile(candidate, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('config.json', json.dumps(config, indent=2))
            for name, path in paths.items():
                archive.write(path, name)
        result = check(candidate)
        with output.open('xb') as stream:
            stream.write(candidate.read_bytes())
    return {**result, 'output': str(output), 'files': sorted([*paths, 'config.json'])}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', default='.')
    parser.add_argument('--reference', default='weights-reference.json')
    parser.add_argument('--output', default='inference.zip')
    parser.add_argument('--include', action='append', default=[], help='Additional dependency path relative to source')
    args = parser.parse_args()
    print(json.dumps(build(args.source, args.reference, args.output, args.include), indent=2))
