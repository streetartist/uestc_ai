"""Private weight upload and trial handoff, using local HTTP/SQLite fixtures."""
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_problem_setup as setup
from platform_api.extensions import db
from platform_api.models import EvaluationRun, Team
from platform_api.model_weights import CHUNK_BYTES, ModelWeightAsset
from evaluation_adapters.classification_runner import extract_package, install_uploaded_weights
from check_classification_submission import check
from prepare_classification_submission import build


class WeightUploadTests(unittest.TestCase):
    setUp = setup.ProblemSetupTests.setUp
    tearDown = setup.ProblemSetupTests.tearDown
    login = setup.ProblemSetupTests.login
    register = setup.ProblemSetupTests.register
    fixture = setup.ProblemSetupTests.fixture
    prepare = setup.ProblemSetupTests.prepare
    document = setup.ProblemSetupTests.document

    def ready(self):
        self.prepare()
        self.app.config['CLASSIFICATION_WEIGHT_UPLOAD_ENABLED'] = True
        self.config['resources'].update(gpu=True, episodes=1)
        self.config['max_team_runs'] = 3
        self.runtime_config = {'execution': 'autodl-native', 'image': 'image-private-weights-v2', 'agent_image': '',
            'scenarios': [{'dataset': 'depth-ir-v2', 'manifest_sha256': 'd' * 64}]}
        saved = self.admin.put(self.url, json=self.document())
        self.assertEqual(saved.status_code, 200, saved.get_json())

    def create(self, raw, **changes):
        return self.member.post(f"/api/problems/{self.problem['id']}/model-weights", json={
            'team_id': self.team['id'], 'name': 'student.pt', 'size': len(raw),
            'sha256': hashlib.sha256(raw).hexdigest(), **changes})

    def finish(self, raw):
        created = self.create(raw)
        self.assertEqual(created.status_code, 201, created.get_json())
        identity = created.get_json()['id']
        for i in range((len(raw)+CHUNK_BYTES-1)//CHUNK_BYTES):
            response = self.member.put(f'/api/model-weights/{identity}/chunks/{i}',
                data=raw[i*CHUNK_BYTES:(i+1)*CHUNK_BYTES], content_type='application/octet-stream')
            self.assertEqual(response.status_code, 204)
        completed = self.member.post(f'/api/model-weights/{identity}/complete')
        self.assertEqual(completed.status_code, 200, completed.get_json())
        return {k: completed.get_json()[k] for k in ('id', 'sha256', 'size')}

    def code_zip(self, ref):
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as archive:
            archive.writestr('inference.py', 'class Model:\n def predict(self, sample_path): return [1.0] * 40\n')
            archive.writestr('config.json', json.dumps({'num_classes': 40, 'weights_asset': ref}))
        return output.getvalue()

    def trial(self, ref):
        raw = self.code_zip(ref)
        staged = self.member.post('/api/submission-assets/stage', data={'file': (io.BytesIO(raw), 'inference.zip')},
                                  content_type='multipart/form-data')
        self.assertEqual(staged.status_code, 201)
        response = self.member.post(f"/api/problems/{self.problem['id']}/evaluation-runs", json={
            'team_id': self.team['id'], 'staged_asset_ids': [staged.get_json()['id']]})
        return response, raw, staged.get_json()['id']

    def test_upload_resume_private_worker_handoff_and_formal_selection(self):
        self.ready()
        raw = b'w' * (CHUNK_BYTES + 13)
        created = self.create(raw).get_json()
        identity = created['id']
        self.assertEqual(self.create(raw).get_json()['id'], identity)
        first = f'/api/model-weights/{identity}/chunks/0'
        self.assertEqual(self.member.put(first, data=raw[:CHUNK_BYTES]).status_code, 204)
        self.assertEqual(self.member.put(first, data=raw[:CHUNK_BYTES]).status_code, 204)
        self.assertEqual(self.member.put(first, data=b'x' * CHUNK_BYTES).status_code, 409)
        self.assertEqual(self.member.get(f'/api/model-weights/{identity}').get_json()['uploaded_chunks'], [0])
        self.assertEqual(self.member.post(f'/api/model-weights/{identity}/complete').status_code, 409)
        self.assertEqual(self.admin.get(f'/api/model-weights/{identity}').status_code, 403)
        self.assertEqual(self.member.put(f'/api/model-weights/{identity}/chunks/1', data=raw[CHUNK_BYTES:]).status_code, 204)
        complete = self.member.post(f'/api/model-weights/{identity}/complete').get_json()
        ref = {k: complete[k] for k in ('id', 'sha256', 'size')}
        self.assertEqual(self.member.put(first, data=raw[:CHUNK_BYTES]).status_code, 409)
        self.assertEqual(self.member.get(self.budget_url).get_json()['used_runs'], 0)
        trial, package, staged_id = self.trial(ref)
        self.assertEqual(trial.status_code, 201, trial.get_json())
        run_id = trial.get_json()['id']
        self.assertEqual(self.member.delete(f'/api/model-weights/{identity}').status_code, 409)
        claimed = self.worker.post('/api/evaluation-worker/claim', headers={'Authorization': 'Bearer test-worker'}, json={
            'adapters': ['classification-v1'], 'gpu': True, 'managed_runtime': True,
            'execution_backends': ['autodl-native'], 'runtime_images': ['image-private-weights-v2'],
            'dataset_manifests': {'depth-ir-v2': 'd'*64}})
        self.assertEqual(claimed.status_code, 200, claimed.get_json())
        job = claimed.get_json()
        self.assertEqual(job['submission']['weights_asset'], ref)
        self.assertEqual(self.worker.get(job['weights_url']).status_code, 403)
        lease = {'X-Evaluation-Lease': job['lease_token']}
        response = self.worker.get(job['weights_url'], headers=lease, buffered=True)
        self.assertEqual(response.data, raw)
        response.close()
        folder = Path(self.runtime.name)
        (folder/'weights.pt').write_bytes(raw)
        (folder/'inference.zip').write_bytes(package)
        self.assertEqual(check(folder/'inference.zip', folder/'weights.pt')['status'], 'passed')
        program = folder/'program'
        program.mkdir()
        config = extract_package(folder/'inference.zip', program)
        install_uploaded_weights(config, program, folder/'weights.pt', ref)
        self.assertEqual(Path(config['weights_path']).read_bytes(), raw)
        completed = self.worker.post(f'/api/evaluation-worker/runs/{run_id}/complete', headers=lease,
            json={'status': 'completed', 'episodes': [{'accuracy': 61}]})
        self.assertEqual(completed.status_code, 200)
        submitted = self.member.post('/api/submissions', json={**self.fields,
            'staged_asset_ids': [staged_id], 'evaluation_run_id': run_id})
        self.assertEqual(submitted.status_code, 201, submitted.get_json())
        self.assertEqual(self.member.get(self.budget_url).get_json()['used_runs'], 1)
        self.assertEqual(self.member.get(self.budget_url).get_json()['remaining_runs'], 2)
        self.assertEqual(self.worker.get(job['weights_url'], headers=lease).status_code, 403)

    def test_hash_size_quota_and_unbound_references(self):
        self.ready()
        self.assertEqual(self.create(b'abc', size=2*1024**3+1).status_code, 400)
        invalid = self.create(b'abc', sha256='a'*64).get_json()['id']
        self.assertEqual(self.member.put(f'/api/model-weights/{invalid}/chunks/0', data=b'xx').status_code, 400)
        self.assertEqual(self.member.put(f'/api/model-weights/{invalid}/chunks/0', data=b'abc').status_code, 204)
        self.assertEqual(self.member.post(f'/api/model-weights/{invalid}/complete').status_code, 400)
        ref = {'id': invalid, 'sha256': 'a'*64, 'size': 3}
        self.assertEqual(self.trial(ref)[0].status_code, 400)
        self.assertEqual(self.member.get(self.budget_url).get_json()['used_runs'], 0)
        self.assertEqual(self.member.delete(f'/api/model-weights/{invalid}').status_code, 204)
        self.app.config['CLASSIFICATION_WEIGHT_TEAM_BYTES'] = 2
        self.assertEqual(self.create(b'abc').status_code, 409)
        self.app.config['CLASSIFICATION_WEIGHT_TEAM_BYTES'] = 3
        pending = self.create(b'ab')
        self.assertEqual(pending.status_code, 201)
        self.assertEqual(pending.get_json()['state'], 'uploading')
        self.assertEqual(self.create(b'cd').status_code, 409)
        self.assertEqual(self.member.delete('/api/model-weights/' + pending.get_json()['id']).status_code, 204)
        self.assertEqual(self.create(b'cd').status_code, 201)

    def test_cross_team_reference_rejected_without_charging_trial(self):
        self.ready()
        ref = self.finish(b'weights')
        with self.app.app_context():
            asset = db.session.get(ModelWeightAsset, ref['id'])
            current = db.session.get(Team, asset.team_id)
            other = Team(competition_id=current.competition_id, name='Other weights team',
                         captain_id=current.captain_id, invite_code='WEIGHTTESTOTHER')
            db.session.add(other)
            db.session.flush()
            asset.team_id = other.id
            db.session.commit()
        trial, _, _ = self.trial(ref)
        self.assertEqual(trial.status_code, 400)
        self.assertEqual(self.member.get(self.budget_url).get_json()['used_runs'], 0)

    def test_checker_rejects_changed_weights_and_ambiguous_source(self):
        self.ready()
        ref = self.finish(b'weights')
        root = Path(self.runtime.name)
        (root/'code.zip').write_bytes(self.code_zip(ref))
        (root/'wrong.pt').write_bytes(b'changed')
        with self.assertRaises(ValueError):
            check(root/'code.zip', root/'wrong.pt')
        config = {'weights_asset': ref, 'weights': {'url': 'https://example.org/weights'}}
        with self.assertRaises(ValueError):
            install_uploaded_weights(config, root, root/'wrong.pt', ref)
        source = root/'source'
        source.mkdir()
        original = {'num_classes': 40, 'weights_file': 'baseline.pt'}
        (source/'config.json').write_text(json.dumps(original))
        (source/'inference.py').write_text('class Model:\n def predict(self,path): return [0]*40\n')
        (source/'baseline.pt').write_bytes(b'weights')
        (root/'reference.json').write_text(json.dumps({'weights_asset': ref}))
        built = build(source, root/'reference.json', root/'submission.zip')
        self.assertEqual(built['weight_source'], 'weights_asset')
        self.assertEqual(json.loads((source/'config.json').read_text()), original)
        with zipfile.ZipFile(root/'submission.zip') as archive:
            self.assertNotIn('baseline.pt', archive.namelist())

    def test_native_worker_verifies_weight_bytes_before_model_execution(self):
        import classification_worker
        self.ready()
        raw = b'private model bytes'
        ref = self.finish(raw)
        package = self.code_zip(ref)
        job = {'id': 'test-run', 'config': self.config, 'runtime': self.runtime_config,
               'submission': {'package_sha256': hashlib.sha256(package).hexdigest(), 'weights_asset': ref},
               'asset_url': '/api/code', 'weights_url': '/api/weight', 'lease_token': 'test-lease'}
        root = Path(self.runtime.name)
        (root/'datasets'/'depth-ir-v2').mkdir(parents=True)
        def download(base, url, lease, destination):
            destination.write_bytes(package if url == '/api/code' else raw)
        def evaluate(*args, **kwargs):
            self.assertEqual(kwargs['trusted_weights'].read_bytes(), raw)
            self.assertEqual(kwargs['weight_reference'], ref)
            return {'accuracy': 61}
        with patch.object(classification_worker, 'download', side_effect=download), \
             patch.object(classification_worker, 'evaluate', side_effect=evaluate) as inference, \
             patch('evaluation_evidence.publish_classification'):
            result = classification_worker.execute('https://platform.example/api', job,
                image_uuid='image-private-weights-v2', dataset_root=root/'datasets',
                gpu='GPU-'+'a'*32, log_root=root/'logs')
            self.assertEqual(result, {'status': 'completed', 'episodes': [{'accuracy': 61}]})
            self.assertEqual(inference.call_count, 1)
        def wrong_download(base, url, lease, destination):
            destination.write_bytes(package if url == '/api/code' else b'wrong')
        with patch.object(classification_worker, 'download', side_effect=wrong_download), \
             patch.object(classification_worker, 'evaluate') as inference:
            with self.assertRaisesRegex(ValueError, 'SHA256'):
                classification_worker.execute('https://platform.example/api', job,
                    image_uuid='image-private-weights-v2', dataset_root=root/'datasets',
                    gpu='GPU-'+'a'*32, log_root=root/'logs')
            inference.assert_not_called()

    def test_cli_resolves_team_and_uploads_through_real_routes(self):
        from upload_classification_weights import upload
        from urllib.parse import urlsplit
        self.ready()
        root = Path(self.runtime.name)
        weights = root/'cli.pt'
        weights.write_bytes(b'cli fixture weights')
        client = self.app.test_client()
        class Opener:
            def open(inner, req, timeout=None):
                response = client.open(urlsplit(req.full_url).path, method=req.method,
                    data=req.data, content_type=req.get_header('Content-type'))
                self.assertLess(response.status_code, 300, response.get_json())
                stream = io.BytesIO(response.data)
                stream.status = response.status_code
                return stream
        with patch('urllib.request.build_opener', return_value=Opener()):
            reference = upload('https://platform.example/api', weights, self.problem['slug'], None,
                               'limits@std.uestc.edu.cn', 'secure-pass-123')
            same = upload('https://platform.example/api', weights, self.problem['id'], self.team['name'],
                          'limits@std.uestc.edu.cn', 'secure-pass-123', reference['weights_asset']['id'])
        self.assertEqual(reference, same)
        self.assertEqual(self.member.get(self.budget_url).get_json()['used_runs'], 0)

    def test_team_deletion_includes_weight_files(self):
        from platform_api.deletion import _Scope, remove_upload_files
        self.ready()
        ref = self.finish(b'team weight bytes')
        with self.app.app_context():
            asset = db.session.get(ModelWeightAsset, ref['id'])
            stored = Path(self.app.config['UPLOAD_FOLDER'])/asset.storage_name
            self.assertTrue(stored.is_file())
            scope = _Scope([self.team['id']])
            names = scope.files()
            self.assertIn(asset.storage_name, names)
            scope.delete_all()
            db.session.commit()
            remove_upload_files(names)
            self.assertIsNone(db.session.get(ModelWeightAsset, ref['id']))
            self.assertFalse(stored.exists())


if __name__ == '__main__':
    unittest.main()
