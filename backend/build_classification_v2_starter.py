"""Package a verified depth/IR baseline export and the matching public data installer."""
import argparse
import json
import zipfile
from pathlib import Path, PurePosixPath

from install_classification_v2_data import sha256
from classification_v2_student_runtime import RUNTIME_FILES

ROOT = Path(__file__).resolve().parent


def build(baseline, output):
    baseline, output = Path(baseline), Path(output)
    manifest = json.loads((baseline / 'manifest.json').read_text(encoding='utf-8'))
    audit = json.loads((baseline / 'audit.json').read_text(encoding='utf-8'))
    if audit.get('status') != 'passed' or audit.get('smoke_data') or not 0 < audit.get('parameters', 0) <= 100000000:
        raise ValueError('A real validated baseline with at most 100M inference parameters is required')
    files = {}
    for item in manifest['files']:
        relative = PurePosixPath(item['path'])
        if relative.is_absolute() or '..' in relative.parts or '\\' in item['path'] or item['path'] in files:
            raise ValueError('Invalid exported baseline path')
        path = baseline.joinpath(*relative.parts)
        if (path.is_symlink() or baseline.resolve() not in path.resolve().parents
                or path.stat().st_size != item['bytes'] or sha256(path) != item['sha256']):
            raise ValueError('Exported baseline file differs from its manifest')
        files[item['path']] = path
    required = {'inference.py', 'config.json', 'baseline.pt', 'README.md', 'predict.py',
                'analyze_predictions.py', 'preview_sample.py', 'metrics.json', 'environment.json', 'audit.json',
                'raw_model.py', 'ig65m_backbone.py', 'depth_ir_io.py', 'build_cache.py',
                'training/train_raw.py', 'training/prepare_training_manifest.py',
                'training/build_cache.py', 'training/raw_model.py',
                'training/ig65m_backbone.py', 'training/depth_ir_io.py', 'training/make_manifest.py'}
    if not required <= set(files):
        raise ValueError('Baseline export is missing training, prediction, or analysis files')
    for name in ('raw_model.py', 'ig65m_backbone.py', 'depth_ir_io.py', 'build_cache.py'):
        if files[name].read_bytes() != files['training/' + name].read_bytes():
            raise ValueError('Training and inference source differ: ' + name)
    config = json.loads((baseline / 'config.json').read_text(encoding='utf-8'))
    if config.get('input_version') != 2 or config.get('weights_file') != 'baseline.pt':
        raise ValueError('Student package requires the local version-2 weights configuration')
    if config.get('person_crop') and config.get('detector_file') not in files:
        raise ValueError('Person-crop baseline requires its verified detector weights')
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'x', zipfile.ZIP_DEFLATED) as archive:
        for name, path in sorted(files.items()):
            if name in RUNTIME_FILES:
                raise ValueError('Baseline export must leave shared runtime files to the package builder')
            archive.write(path, name)
        for name, text in RUNTIME_FILES.items():
            archive.writestr(name, text)
        archive.write(baseline / 'manifest.json', 'baseline-manifest.json')
        for name in ('install_classification_v2_shards.py', 'install_classification_v2_data.py',
                     'upload_classification_weights.py', 'check_classification_submission.py',
                     'prepare_classification_submission.py', 'prepare_classification_inference_config.py'):
            archive.write(ROOT / name, name)
        archive.writestr('evaluation_adapters/__init__.py', '')
        archive.write(ROOT / 'evaluation_adapters/depth_ir_dataset.py', 'evaluation_adapters/depth_ir_dataset.py')
        for name in ('classification_runner.py', 'classification_assets.py'):
            archive.write(ROOT / 'evaluation_adapters' / name, 'evaluation_adapters/' + name)
    return {'file': output.name, 'bytes': output.stat().st_size, 'sha256': sha256(output)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.baseline, args.output)))
