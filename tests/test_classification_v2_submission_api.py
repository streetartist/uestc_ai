"""Local HTTP/DB integration; the worker completion is synthetic, not a GPU run."""
import hashlib
import io
import json
import sys
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_problem_setup as setup
from evaluation_adapters.classification_runner import extract_package
from platform_api.extensions import db
from platform_api.models import EvaluationRun, SubmissionVersion


class ClassificationV2SubmissionAPITests(unittest.TestCase):
    setUp = setup.ProblemSetupTests.setUp
    tearDown = setup.ProblemSetupTests.tearDown
    login = setup.ProblemSetupTests.login
    register = setup.ProblemSetupTests.register
    fixture = setup.ProblemSetupTests.fixture
    prepare = setup.ProblemSetupTests.prepare
    document = setup.ProblemSetupTests.document

    def test_upload_versioned_trial_worker_and_formal_submission(self):
        self.prepare()
        self.config['resources'].update(gpu=True, episodes=1)
        self.config['metrics'] = ['accuracy', 'macro_f1', 'latency_ms']
        self.runtime_config = {'execution': 'autodl-native', 'image': 'image-local-fixture-depth-ir-v2',
                               'agent_image': '', 'scenarios': [{'dataset': 'compact-depth-ir-v2',
                                                               'manifest_sha256': 'd' * 64}]}
        configured = self.admin.put(self.url, json=self.document())
        self.assertEqual(configured.status_code, 200, configured.get_json())
        fields = json.loads((Path(__file__).resolve().parents[1] / 'docs/cuhk-depth-ir-v2-submission-fields.json').read_text(encoding='utf-8'))
        fields['attachments'] = [{'key': 'inference', 'label': 'Inference ZIP', 'extensions': ['zip'],
                                  'min_count': 1, 'max_count': 1, 'required_files': ['inference.py', 'config.json']}]
        changed = self.admin.patch(f"/api/manage/problems/{self.problem['id']}", json={
            'submission_schema': fields, 'judging_schema': {'rubric': {'项目成果': 1}},
            'scoring_config': {'review_score_mode': 'weighted',
                              'performance_scoring': {'preset': 'wujie-depth-v1', 'criterion': '项目成果'}}})
        self.assertEqual(changed.status_code, 200, changed.get_json())
        model_config = {'num_classes': 40, 'channels': 4, 'frames': 16, 'person_crop': False,
                        'weights': {'url': 'https://models.example.org/baseline-v2.pt',
                                    'sha256': 'a' * 64, 'size': 256000000}}
        package = io.BytesIO()
        with zipfile.ZipFile(package, 'w') as archive:
            archive.writestr('inference.py', 'class Model:\n def __init__(self,config,device): pass\n def predict(self,sample_path): return [1.0]+[0.0]*39\n')
            archive.writestr('config.json', json.dumps(model_config))
        raw = package.getvalue()
        staged = self.member.post('/api/submission-assets/stage', data={
            'file': (io.BytesIO(raw), 'depth-ir-v2.zip')}, content_type='multipart/form-data')
        self.assertEqual(staged.status_code, 201, staged.get_json())
        asset_id = staged.get_json()['id']
        trial = self.member.post(f"/api/problems/{self.problem['id']}/evaluation-runs", json={
            'team_id': self.team['id'], 'staged_asset_ids': [asset_id]})
        self.assertEqual(trial.status_code, 201, trial.get_json())
        run_id = trial.get_json()['id']
        with self.app.app_context():
            run = db.session.get(EvaluationRun, run_id)
            self.assertEqual(run.runtime_snapshot, self.runtime_config)
            self.assertEqual(run.config_snapshot['adapter'], 'classification-v1')
            self.assertEqual(run.submission_snapshot['package_sha256'], hashlib.sha256(raw).hexdigest())
        # Existing trial locks the runtime version, including its private data hash.
        mutated = self.document()
        mutated['runtime']['scenarios'][0]['manifest_sha256'] = 'e' * 64
        self.assertEqual(self.admin.put(self.url, json=mutated).status_code, 409)
        headers = {'Authorization': 'Bearer test-worker'}
        capabilities = {'adapters': ['classification-v1'], 'gpu': True, 'managed_runtime': True,
                        'execution_backends': ['autodl-native'], 'runtime_images': ['old-image'],
                        'dataset_manifests': {'compact-depth-ir-v2': 'd' * 64}}
        self.assertEqual(self.worker.post('/api/evaluation-worker/claim', headers=headers, json=capabilities).status_code, 204)
        capabilities['runtime_images'] = ['image-local-fixture-depth-ir-v2']
        capabilities['dataset_manifests'] = {'compact-depth-ir-v2': 'e' * 64}
        self.assertEqual(self.worker.post('/api/evaluation-worker/claim', headers=headers, json=capabilities).status_code, 204)
        capabilities['dataset_manifests'] = {'compact-depth-ir-v2': 'd' * 64}
        claimed = self.worker.post('/api/evaluation-worker/claim', headers=headers, json=capabilities)
        self.assertEqual(claimed.status_code, 200, claimed.get_json())
        job = claimed.get_json()
        self.assertEqual(job['runtime'], self.runtime_config)
        public = self.member.get(f'/api/evaluation-runs/{run_id}')
        self.assertEqual(public.status_code, 200)
        self.assertNotIn('image-local-fixture-depth-ir-v2', public.get_data(as_text=True))
        self.assertNotIn('d' * 64, public.get_data(as_text=True))
        lease = {'X-Evaluation-Lease': job['lease_token']}
        self.assertEqual(self.worker.get(job['asset_url']).status_code, 403)
        downloaded = self.worker.get(job['asset_url'], headers=lease, buffered=True)
        self.assertEqual(downloaded.data, raw)
        downloaded.close()
        artifact = Path(self.runtime.name) / 'downloaded.zip'
        artifact.write_bytes(raw)
        self.assertEqual(extract_package(artifact, Path(self.runtime.name) / 'program'), model_config)
        # Inject a deterministic worker result to test persistence and score wiring.
        completed = self.worker.post(f"/api/evaluation-worker/runs/{run_id}/complete", headers=lease,
            json={'status': 'completed', 'episodes': [{'accuracy': 80, 'macro_f1': 70, 'latency_ms': 200}]})
        self.assertEqual(completed.status_code, 200, completed.get_json())
        form = {**self.fields, 'staged_asset_ids': [asset_id], 'evaluation_run_id': run_id}
        self.assertEqual(self.member.post('/api/submissions', json=form).status_code, 400)
        form['fields'] = {'inference_model_inventory': 'R34; 64M; https://models.example.org; SHA256; python count.py'}
        submitted = self.member.post('/api/submissions', json=form)
        self.assertEqual(submitted.status_code, 201, submitted.get_json())
        with self.app.app_context():
            version = db.session.get(SubmissionVersion, submitted.get_json()['version']['id'])
            self.assertEqual(version.fields, form['fields'])
            self.assertEqual(version.status, 'submitted')
            self.assertEqual(len(version.assets), 1)
            stored = Path(self.app.config['UPLOAD_FOLDER']) / version.assets[0].storage_name
            self.assertEqual(stored.read_bytes(), raw)
            self.assertEqual(db.session.get(EvaluationRun, run_id).runtime_snapshot, self.runtime_config)
        detail = self.member.get(f"/api/works/{submitted.get_json()['id']}").get_json()
        self.assertEqual(detail['evaluation']['performance']['score'], 75)
        self.assertEqual(self.member.get(self.budget_url).get_json()['used_runs'], 1)


if __name__ == '__main__':
    unittest.main()


