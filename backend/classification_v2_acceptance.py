"""Run a real exported baseline through the Linux GPU judge on public validation clips."""
import argparse
import importlib.util
import json
import os
import shutil
import tempfile
import time
import urllib.parse
import zipfile
from pathlib import Path

from evaluation_adapters.classification_runner import digest_file, evaluate
from evaluation_adapters.depth_ir_dataset import clip_digest


def run(args):
    started = time.perf_counter()
    if os.geteuid() != 0:
        raise RuntimeError('The production judge requires Linux root and a dedicated idle GPU')
    if args.output.exists():
        raise FileExistsError('Use a new acceptance output directory')
    data = json.loads((args.validation / 'manifest.json').read_text())
    if data.get('version') != 2 or data.get('modality') != 'depth_ir':
        raise ValueError('Expected the published v2 validation split')
    count = min(args.samples, len(data['samples']))
    if count < 1:
        raise ValueError('At least one sample is required')
    indexes = [round(i * (len(data['samples']) - 1) / max(1, count - 1)) for i in range(count)]
    selected = [data['samples'][i] for i in indexes]
    config = json.loads((args.baseline / 'config.json').read_text())
    weights = args.baseline / config.pop('weights_file')
    config.pop('weights', None)
    config.pop('weights_path', None)
    output = args.output.resolve()
    with tempfile.TemporaryDirectory(prefix='cuhk-v2-private-') as private_dir, \
            tempfile.TemporaryDirectory(prefix='cuhk-v2-public-weight-') as weight_dir:
        private, public_weight = Path(private_dir), Path(weight_dir)
        private.chmod(0o700)
        dataset = private / 'validation'
        dataset.mkdir(mode=0o700)
        for row in selected:
            source = args.validation / row['file']
            if clip_digest(source, verify_images=True) != row['sha256']:
                raise ValueError('Published validation clip hash differs')
            shutil.copytree(source, dataset / row['file'])
        (dataset / 'manifest.json').write_text(json.dumps({**data, 'samples': selected}))
        (dataset / 'manifest.json').chmod(0o600)
        weight_hash = digest_file(weights)
        hosts = []
        if args.weights_url:
            parsed = urllib.parse.urlparse(args.weights_url)
            if parsed.scheme != 'https' or not parsed.hostname:
                raise ValueError('Published weights require an HTTPS URL')
            config['weights'] = {'url': args.weights_url, 'sha256': weight_hash, 'size': weights.stat().st_size}
            hosts = args.model_host or [parsed.hostname]
        else:
            # Local acceptance exercises the actual sandbox and model without
            # publishing experimental weights. Download validation is separate.
            public_weight.chmod(0o755)
            staged = public_weight / 'baseline.pt'
            shutil.copyfile(weights, staged)
            staged.chmod(0o644)
            config['weights_path'] = str(staged)
        package = private / 'inference.zip'
        names = ['inference.py', 'raw_model.py', 'ig65m_backbone.py', 'depth_ir_io.py', 'build_cache.py']
        if config.get('person_crop'):
            names.append(config['detector_file'])
        with zipfile.ZipFile(package, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name in names:
                if Path(name).name != name:
                    raise ValueError('Baseline files must be at the package root')
                archive.write(args.baseline / name, name)
            archive.writestr('config.json', json.dumps(config))
        judge_started = time.perf_counter()
        metrics = evaluate(package, dataset, digest_file(dataset / 'manifest.json'), output,
                           gpu=args.gpu, seconds=args.seconds, memory_mb=args.memory_mb, cpus=args.cpus,
                           uid=args.uid, model_hosts=hosts)
        # Load the exact platform score function without requiring the Flask app.
        spec = importlib.util.spec_from_file_location('score', Path(__file__).parent / 'platform_api/performance_scoring.py')
        scoring = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(scoring)
        judge_seconds = time.perf_counter() - judge_started
        evidence = json.loads((output / 'evidence.json').read_text())
        truth = {row['file']: row['label'] for row in selected}
        correct = sum(row['prediction'] == truth[row['file']] for row in evidence['predictions'])
        full = count == len(data['samples'])
        result = {'status': 'passed', 'scope': ('full public validation' if full else 'public validation subset') + '; platform isolation acceptance',
                  'full_public_validation': full, 'correct': correct,
                  'samples': count, 'metrics': metrics,
                  'project_score': scoring.performance_result({'preset': 'wujie-depth-v1', 'criterion': '项目成果'},
                                                             'classification-v1', [metrics]),
                  'weight_transport': 'published_https' if args.weights_url else 'pre_staged_local_readonly',
                  'weights_sha256': weight_hash, 'validation_manifest_sha256': digest_file(args.validation / 'manifest.json'),
                  'source_samples': [s['file'] for s in selected],
                  'wall_seconds': time.perf_counter() - started, 'judge_wall_seconds': judge_seconds,
                  'timing_scope': 'metrics.latency_ms: mean per-sample image reading, preprocessing, detection, classification, CUDA synchronization and IPC; first sample excluded. judge_wall_seconds also includes initialization, input staging and teardown.',
                  'prediction_evidence': 'evidence.json',
                  'sample_id_mapping': 'evidence.predictions[].file + .npy = deployment_predictions.npz sample_ids'}
        (output / 'acceptance.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
        return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('baseline', 'validation', 'output'):
        parser.add_argument('--' + key, type=Path, required=True)
    parser.add_argument('--gpu', required=True, help='Reserved idle GPU UUID')
    parser.add_argument('--samples', type=int, default=8)
    parser.add_argument('--seconds', type=int, default=1800)
    parser.add_argument('--memory-mb', type=int, default=6144)
    parser.add_argument('--cpus', type=int, default=4)
    parser.add_argument('--uid', type=int, default=20000, help='Distinct unprivileged UID for each concurrent acceptance')
    parser.add_argument('--weights-url')
    parser.add_argument('--model-host', action='append', help='Approved download/redirect host, repeatable')
    print(json.dumps(run(parser.parse_args()), ensure_ascii=False))
