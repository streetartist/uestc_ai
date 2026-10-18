"""Measure a frozen CUHK baseline on one reserved evaluation GPU."""
import argparse
import importlib.metadata
import platform
import json
import os
import shutil
import statistics
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path

from evaluation_adapters.classification_runner import digest_file, evaluate
from evaluation_adapters.depth_ir_dataset import validate_manifest


def write(path, value):
    temporary = path.with_suffix('.writing')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temporary.replace(path)


def main(a):
    started = time.time()
    if os.geteuid() != 0:
        raise RuntimeError('Use the organizer Linux judge environment')
    if a.output.exists():
        raise FileExistsError('Preserve previous results and use a new output directory')
    a.output.mkdir(parents=True, mode=0o700)
    os.umask(0o077)
    progress = a.output / 'progress.json'
    write(progress, {'stage': 'preparing', 'started': started})
    config = json.loads((a.baseline / 'config.json').read_text())
    weights = a.baseline / config.pop('weights_file')
    if digest_file(weights) != a.weights_sha256:
        raise ValueError('Frozen baseline weights differ')
    if digest_file(a.test_archive) != a.test_archive_sha256:
        raise ValueError('Private test archive differs')
    if config.get('person_crop'):
        raise ValueError('This calibration fixes the selected full-frame baseline')
    with tempfile.TemporaryDirectory(prefix='wujie-calibration-input-') as private_directory:
        private = Path(private_directory)
        test = private / 'test'
        test.mkdir(mode=0o700)
        with zipfile.ZipFile(a.test_archive) as z:
            for row in z.infolist():
                relative = Path(row.filename)
                if relative.is_absolute() or '..' in relative.parts or '\\' in row.filename:
                    raise ValueError('Invalid dataset member')
                target = test / relative
                if row.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(row) as source, target.open('xb') as output:
                    shutil.copyfileobj(source, output)
        data = json.loads((test / 'manifest.json').read_text())
        if len(data['samples']) != 470 or digest_file(test / 'manifest.json') != a.test_manifest_sha256:
            raise ValueError('Expected the fixed 470-clip organizer test split')
        validate_manifest(test, data)
        validation = private / 'validation'
        shutil.copytree(a.validation, validation)
        validation.chmod(0o700)
        vd = json.loads((validation / 'manifest.json').read_text())
        if len(vd['samples']) != 466 or vd['classes'] != data['classes']:
            raise ValueError('Validation split or class order differs')
        validate_manifest(validation, vd)
        gpu_info = subprocess.check_output(['nvidia-smi', '-i', a.gpu, '--query-gpu=name,uuid,driver_version,memory.total', '--format=csv,noheader,nounits'], text=True).strip()
        sources = {str(p.relative_to(Path(__file__).parent)): digest_file(p)
                   for p in [Path(__file__), *sorted((Path(__file__).parent / 'evaluation_adapters').glob('classification*.py')), Path(__file__).parent / 'evaluation_adapters/depth_ir_dataset.py']}
        with tempfile.TemporaryDirectory(prefix='wujie-baseline-frozen-') as directory:
            staged = Path(directory)
            staged.chmod(0o755)
            weight_copy = staged / 'baseline.pt'
            shutil.copyfile(weights, weight_copy)
            weight_copy.chmod(0o644)
            for name in ('weights', 'weights_asset', 'weights_path'):
                config.pop(name, None)
            config['weights_path'] = str(weight_copy)
            package = a.output / 'inference.zip'
            files = ['inference.py', 'raw_model.py', 'ig65m_backbone.py', 'depth_ir_io.py', 'build_cache.py']
            with zipfile.ZipFile(package, 'w', zipfile.ZIP_DEFLATED) as z:
                for name in files:
                    z.write(a.baseline / name, name)
                z.writestr('config.json', json.dumps(config))
            records = []
            for label, dataset in [('test', test), ('validation-1', validation), ('validation-2', validation), ('validation-3', validation)]:
                write(progress, {'stage': 'running', 'run': label, 'started': started, 'completed': records})
                before = time.perf_counter()
                metrics = evaluate(package, dataset, digest_file(dataset / 'manifest.json'), a.output / label,
                                   gpu=a.gpu, seconds=1800, memory_mb=6144, cpus=4, uid=22019)
                evidence = json.loads((a.output / label / 'evidence.json').read_text())
                expected = 470 if label == 'test' else 466
                if evidence['sample_count'] != expected:
                    raise ValueError('Incomplete result')
                records.append({'run': label, 'samples': expected, 'metrics': metrics,
                                'judge_wall_seconds': time.perf_counter() - before,
                                'evidence_sha256': digest_file(a.output / label / 'evidence.json')})
                print(json.dumps(records[-1]), flush=True)
        B = records[0]['metrics']['accuracy']
        T = statistics.median(r['metrics']['latency_ms'] for r in records[1:])
        if len({r['metrics']['accuracy'] for r in records[1:]}) != 1:
            raise ValueError('Repeated validation predictions differ')
        result = {'status': 'complete', 'version': 'cuhk-depth-ir-a100-v2-20261019',
                  'reference': {'baseline_accuracy': B, 'gain_span_pp': min(15, 100-B), 'latency_ms': T},
                  'baseline_weights_sha256': a.weights_sha256,
                  'test_archive_sha256': a.test_archive_sha256,
                  'test_manifest_sha256': a.test_manifest_sha256,
                  'validation_manifest_sha256': digest_file(validation / 'manifest.json'),
                  'package_sha256': digest_file(package), 'runner_sources_sha256': sources,
                  'gpu': gpu_info, 'environment': {'python': platform.python_version(), 'packages': {name: importlib.metadata.version(name) for name in ['torch', 'torchvision', 'numpy', 'Pillow', 'timm', 'safetensors']}}, 'resources': {'cpus': 4, 'memory_mb': 6144, 'seconds_per_run': 1800},
                  'timing': 'mean full per-clip inference latency; first warmup clip excluded; median of 3 full-validation means',
                  'weight_transport': 'pre_staged_local_readonly',
                  'runs': records, 'started': started, 'finished': time.time(), 'wall_seconds': time.time()-started}
        write(a.output / 'calibration.json', result)
        write(progress, {'stage': 'complete', 'result': 'calibration.json', 'finished': result['finished']})
        print(json.dumps({'reference':result['reference'],'wall_seconds':result['wall_seconds']}), flush=True)



if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for key in ('baseline','validation','test-archive','output'):
        p.add_argument('--'+key, type=Path, required=True)
    for key in ('gpu','weights-sha256','test-archive-sha256','test-manifest-sha256'):
        p.add_argument('--'+key, required=True)
    args = p.parse_args()
    try:
        main(args)
    except Exception as e:
        if args.output.exists():
            write(args.output / 'failure.json', {'error':type(e).__name__, 'detail':str(e), 'time':time.time()})
        raise
