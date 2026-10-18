import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from evaluation_adapters.depth_ir_dataset import clip_digest, expose_sample, validate_manifest
from evaluation_adapters.classification_runner import classification_metrics, predicted_class, load_dataset
from install_classification_v2_data import install, sha256
from install_classification_v2_shards import install_shards, load_index, download_shard
from prepare_depth_ir_dataset import prepare
from build_classification_v2_starter import build as build_starter
from platform_api.performance_scoring import performance_result


class DepthIRTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.classes = ['action-%02d' % i for i in range(40)]

    def clip(self, path, value):
        for modality in ('Depth_Color', 'IR'):
            (path / modality).mkdir(parents=True)
            for frame in (2, 10):
                shape = (6, 8, 3) if modality == 'Depth_Color' else (6, 8)
                suffix = '_Color' if modality == 'Depth_Color' else ''
                Image.fromarray(np.full(shape, value, dtype=np.uint8)).save(
                    path / modality / f'person007_action_{frame}{suffix}.png')

    def shards(self):
        archives = self.root / 'downloads'
        archives.mkdir()
        index = {'version': 2, 'modality': 'depth_ir', 'processing': {'max_frames': 32, 'resolution': [304, 228]},
                 'splits': {}, 'archives': []}
        for split in ('train', 'validation'):
            source = self.root / 'source' / split
            samples = []
            # A complete class map makes the expected Macro F1 exactly 100.
            for label in range(40):
                folder = source / f'private-subject-{label}'
                self.clip(folder, label)
                samples.append({'file': folder.name, 'label': label, 'sha256': clip_digest(folder)})
            manifest = {'version': 2, 'modality': 'depth_ir', 'classes': self.classes, 'samples': samples}
            (source / 'manifest.json').write_text(json.dumps(manifest))
            (source / 'provenance.json').write_text(json.dumps({'private_source_details': 'organizer metadata'}))
            index['splits'][split] = {'count': 40, 'manifest_sha256': sha256(source / 'manifest.json')}
            for part in range(2):
                archive = archives / f'{split}-{part:03d}.zip'
                with zipfile.ZipFile(archive, 'w') as output:
                    for row in samples[20 * part:20 * (part + 1)]:
                        for file in (source / row['file']).rglob('*.png'):
                            output.write(file, split + '/' + file.relative_to(source).as_posix())
                    if part == 0:
                        for name in ('manifest.json', 'provenance.json'):
                            output.write(source / name, split + '/' + name)
                index['archives'].append({'file': archive.name, 'bytes': archive.stat().st_size, 'sha256': sha256(archive)})
        path = archives / 'dataset-index.json'
        path.write_text(json.dumps(index))
        return archives, load_index(path, sha256(path))

    def test_public_shards_to_contestant_predictions_to_existing_score(self):
        archives, index = self.shards()
        output = self.root / 'installed-validation'
        receipt = install_shards(index, archives, output, 'validation')
        self.assertEqual(receipt['samples'], 40)
        self.assertFalse((output / 'train').exists())
        data = validate_manifest(output, json.loads((output / 'manifest.json').read_text()))
        exposed = self.root / 'exposed'
        exposed.mkdir()
        # A real fresh interpreter reads anonymous PNGs using a contestant Model.
        # This CPU test exercises the data/protocol boundary; Linux GPU isolation
        # remains covered by the runner's deployment smoke test.
        program = self.root / 'contestant.py'
        program.write_text('''import json, sys
from pathlib import Path
from PIL import Image
class Model:
    def predict(self, path):
        p = Path(path)
        assert set(x.name for x in p.iterdir()) == {'Depth_Color','IR'}
        assert [f.name for f in sorted((p/'IR').iterdir())] == ['000000.png','000001.png']
        with Image.open(p/'IR/000000.png') as im:
            label = im.getpixel((0,0))
        return [int(i == label) for i in range(40)]
model = Model()
for line in sys.stdin:
    request = json.loads(line)
    print(json.dumps({'type':'prediction','id':request['id'],'scores':model.predict(request['sample_path'])}), flush=True)
''')
        process = subprocess.Popen([sys.executable, '-I', str(program)], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        predictions = []
        try:
            for i, sample in enumerate(data['samples']):
                path = expose_sample(output, sample, exposed, 2)
                self.assertEqual(path.name, 'clip')
                request = {'id': str(i), 'sample_path': str(path)}
                process.stdin.write(json.dumps(request) + '\n')
                process.stdin.flush()
                response = process.stdout.readline()
                if not response:
                    self.fail('Contestant interpreter failed: ' + process.stderr.read())
                predictions.append(predicted_class(json.loads(response), str(i)))
            process.stdin.close()
            self.assertEqual(process.wait(timeout=15), 0, process.stderr.read())
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            if not process.stdin.closed:
                process.stdin.close()
            process.stdout.close()
            process.stderr.close()
        metrics, _ = classification_metrics([s['label'] for s in data['samples']], predictions)
        self.assertEqual(metrics, {'accuracy': 100, 'macro_f1': 100})
        score = performance_result({'preset': 'wujie-depth-v1', 'criterion': '项目成果'},
                                   'classification-v1', [{**metrics, 'latency_ms': 200}])
        self.assertEqual(score['score'], 95)

    def test_synchronized_frame_numbers_required(self):
        folder = self.root / 'clip'
        self.clip(folder, 1)
        path = folder / 'IR/person007_action_10.png'
        path.rename(path.with_name('person007_action_11.png'))
        with self.assertRaisesRegex(ValueError, 'frame numbers must match'):
            clip_digest(folder)

    def test_manifest_rejects_bad_types_without_crashing(self):
        for data in (None, [], {'version': 2, 'classes': [{}] * 40, 'modality': 'depth_ir', 'samples': [None]}):
            with self.assertRaises(ValueError):
                validate_manifest(self.root, data)

    def test_hash_and_duplicate_members_are_checked(self):
        archives, index = self.shards()
        shard = archives / 'validation-001.zip'
        with zipfile.ZipFile(shard, 'a') as archive:
            archive.writestr('validation/../escape.png', b'bad')
        with self.assertRaisesRegex(ValueError, 'Shard SHA256'):
            install_shards(index, archives, self.root / 'bad-hash', 'validation')
        self.assertFalse((self.root / 'bad-hash.installing').exists())
        item = next(x for x in index['archives'] if x['file'] == shard.name)
        item.update(bytes=shard.stat().st_size, sha256=sha256(shard))
        with self.assertRaisesRegex(ValueError, 'outside selected split'):
            install_shards(index, archives, self.root / 'bad-path', 'validation')
        self.assertFalse((self.root / 'escape.png').exists())

    def test_download_checks_bytes_and_reuses_complete_file(self):
        payload = b'a real shard payload'
        import hashlib
        item = {'file': 'train-000.zip', 'bytes': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()}
        response = io.BytesIO(payload)
        response.url = 'https://example.org/train-000.zip'
        with patch('urllib.request.urlopen', return_value=response) as request:
            target = download_shard('https://example.org/data', item, self.root / 'cache')
            self.assertEqual(target.read_bytes(), payload)
            download_shard('https://example.org/data', item, self.root / 'cache')
            self.assertEqual(request.call_count, 1)

    def test_private_preparation_tar_and_legacy_input(self):
        clips = self.root / 'clips'
        self.clip(clips / 'x', 5)
        classes, labels = self.root / 'classes.json', self.root / 'labels.csv'
        classes.write_text(json.dumps(self.classes))
        labels.write_text('file,label\nx,5\n')
        prepared = self.root / 'prepared'
        digest = prepare(clips, labels, classes, prepared)
        archive = self.root / 'private.tar.gz'
        with tarfile.open(archive, 'w:gz') as output:
            output.add(prepared, arcname='depth-ir-v2')
        installed = self.root / 'private-installed'
        self.assertEqual(install(archive, installed, sha256(archive), digest, 'depth-ir-v2')['samples'], 1)
        legacy = self.root / 'legacy'
        legacy.mkdir()
        np.save(legacy / 'old.npy', np.zeros((16, 1, 112, 112), dtype=np.float32))
        exposed = self.root / 'exposed'
        exposed.mkdir()
        self.assertEqual(np.load(expose_sample(legacy, {'file': 'old.npy'}, exposed, 1)).shape, (16, 1, 112, 112))

    def test_starter_requires_audited_export_and_keeps_local_weights_configuration(self):
        baseline = self.root / 'baseline'
        baseline.mkdir()
        names = ('inference.py', 'config.json', 'baseline.pt', 'README.md', 'predict.py',
                 'analyze_predictions.py', 'preview_sample.py', 'metrics.json', 'environment.json', 'audit.json',
                 'raw_model.py', 'ig65m_backbone.py', 'depth_ir_io.py', 'build_cache.py',
                 'training/train_raw.py', 'training/prepare_training_manifest.py',
                 'training/build_cache.py', 'training/raw_model.py',
                 'training/ig65m_backbone.py', 'training/depth_ir_io.py', 'training/make_manifest.py')
        for name in names:
            path = baseline / name
            path.parent.mkdir(exist_ok=True)
            path.write_text('{}')
        (baseline / 'config.json').write_text(json.dumps({'input_version': 2, 'weights_file': 'baseline.pt'}))
        (baseline / 'audit.json').write_text(json.dumps({'status': 'passed', 'parameters': 66138780, 'smoke_data': False}))
        records = [{'path': name, 'bytes': (baseline / name).stat().st_size, 'sha256': sha256(baseline / name)} for name in names]
        (baseline / 'manifest.json').write_text(json.dumps({'files': records}))
        package = self.root / 'starter.zip'
        build_starter(baseline, package)
        with zipfile.ZipFile(package) as archive:
            self.assertIn('install_classification_v2_shards.py', archive.namelist())
            self.assertIn('preview_sample.py', archive.namelist())
            self.assertIn('setup.sh', archive.namelist())
            self.assertIn('timm==1.0.27', archive.read('requirements.txt').decode())
            self.assertIn('safetensors==0.8.0', archive.read('requirements.txt').decode())
            self.assertIn('ModelEmaV3', archive.read('check_environment.py').decode())
            self.assertEqual(json.loads(archive.read('config.json'))['weights_file'], 'baseline.pt')
        (baseline / 'manifest.json').write_text(json.dumps({'files': [r for r in records if r['path'] != 'training/build_cache.py']}))
        with self.assertRaisesRegex(ValueError, 'missing training'):
            build_starter(baseline, self.root / 'inference-only.zip')
        (baseline / 'manifest.json').write_text(json.dumps({'files': [r for r in records if r['path'] != 'preview_sample.py']}))
        with self.assertRaisesRegex(ValueError, 'missing training'):
            build_starter(baseline, self.root / 'without-preview.zip')
        (baseline / 'manifest.json').write_text(json.dumps({'files': records}))
        (baseline / 'baseline.pt').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'differs from its manifest'):
            build_starter(baseline, self.root / 'tampered.zip')


if __name__ == '__main__':
    unittest.main()
