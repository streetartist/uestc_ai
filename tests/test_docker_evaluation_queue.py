import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_independent_evaluation_trials as trial_fixtures
from platform_api.extensions import db
from platform_api.models import EvaluationRun, EvaluationWorkerState, Problem, utcnow
from evaluation_worker_lock import acquire_worker_lock


class DockerQueueTests(unittest.TestCase):
    setUp = trial_fixtures.IndependentTrialTests.setUp
    tearDown = trial_fixtures.IndependentTrialTests.tearDown
    login = trial_fixtures.IndependentTrialTests.login
    register = trial_fixtures.IndependentTrialTests.register
    fixture = trial_fixtures.IndependentTrialTests.fixture
    stage = trial_fixtures.IndependentTrialTests.stage
    start = trial_fixtures.IndependentTrialTests.start

    def prepare_two_tracks(self):
        self.fixture()
        self.app.config['EVALUATION_ENABLED_ADAPTERS'] = 'minecraft-agent-v1,classification-v1'
        self.caps = {'worker_id': 'docker-node-one', 'adapters': ['minecraft-agent-v1', 'classification-v1'],
            'gpu': False, 'managed_runtime': True, 'api_proxy': False}
        first = self.start(self.stage()).get_json()
        self.first_id = first['id']
        with self.app.app_context():
            source = db.session.get(Problem, self.problem['id'])
            second = Problem(track_id=source.track_id, slug='queue-classification', code='Q2', title='Second adapter', status='published',
                evaluation_config={'adapter':'classification-v1','task':'classification', 'max_team_runs':2,
                    'resources':{'cpus':1,'memory_mb':512,'gpu':False,'time_seconds':60,'episodes':1},
                    'api':{'enabled':False,'max_calls':0},'metrics':['accuracy']})
            db.session.add(second);db.session.commit()
            self.problem_b=second.id
        self.problem={'id':self.problem_b}
        second = self.start(self.stage()).get_json()
        self.second_id=second['id']

    def claim(self, node='docker-node-one'):
        return self.app.test_client().post('/api/evaluation-worker/claim',
            headers={'Authorization':'Bearer test-worker'}, json={**self.caps,'worker_id':node})

    def complete(self, job):
        metric='task_success' if job['config']['adapter']=='minecraft-agent-v1' else 'accuracy'
        response=self.app.test_client().post(f"/api/evaluation-worker/runs/{job['id']}/complete",
            headers={'X-Evaluation-Lease':job['lease_token']},json={'status':'completed','episodes':[{metric:10}]})
        self.assertEqual(response.status_code,200,response.get_json())

    def test_shared_node_fifo_across_problems_and_queue_feedback_private(self):
        self.prepare_two_tracks()
        first=self.claim().get_json()
        self.assertEqual(first['id'],self.first_id)
        self.assertEqual(self.claim().status_code,204)
        waiting=self.member.get(f'/api/evaluation-runs/{self.second_id}').get_json()
        self.assertEqual(waiting['status'],'queued')
        self.assertEqual(waiting['attempts'],0)
        self.assertIsNone(waiting['started_at'])
        self.assertEqual(waiting['dispatch']['ahead'],1)
        self.assertNotIn(self.first_id,str(waiting['dispatch']))
        self.assertEqual(self.app.test_client().get(f'/api/evaluation-runs/{self.second_id}').status_code,401)
        self.complete(first)
        waiting=self.member.get(f'/api/evaluation-runs/{self.second_id}').get_json()
        self.assertEqual(waiting['dispatch']['ahead'],0)
        second=self.claim().get_json()
        self.assertEqual(second['id'],self.second_id)
        self.complete(second)
        with self.app.app_context():
            self.assertEqual(db.session.get(EvaluationRun,self.first_id).submission_version.submission.evaluation_runs_used,1)
            self.assertEqual(db.session.get(EvaluationRun,self.second_id).submission_version.submission.evaluation_runs_used,1)

    def test_concurrent_requests_same_node_never_fill_second_slot(self):
        self.prepare_two_tracks()
        with ThreadPoolExecutor(max_workers=2) as executor:
            responses=list(executor.map(lambda _:self.claim(),range(2)))
        self.assertEqual(sorted(r.status_code for r in responses),[200,204])
        claimed=next(r.get_json() for r in responses if r.status_code==200)
        self.assertEqual(claimed['id'],self.first_id)
        with self.app.app_context():
            self.assertEqual(EvaluationRun.query.filter_by(status='running',worker_id='docker-node-one').count(),1)
            self.assertEqual(db.session.get(EvaluationRun,self.second_id).status,'queued')

    def test_different_nodes_can_claim_independent_jobs(self):
        self.prepare_two_tracks()
        first=self.claim().get_json()
        second=self.claim('docker-node-two').get_json()
        self.assertEqual([first['id'],second['id']],[self.first_id,self.second_id])
        self.assertEqual(self.claim().status_code,204)
        self.assertEqual(self.claim('docker-node-two').status_code,204)

    def test_offline_queue_survives_and_expired_lease_recovers_before_next_job(self):
        self.prepare_two_tracks()
        first=self.claim().get_json()
        with self.app.app_context():
            db.session.get(EvaluationWorkerState,'docker-node-one').seen_at=utcnow()-timedelta(seconds=76)
            db.session.get(EvaluationRun,self.first_id).lease_expires_at=utcnow()-timedelta(seconds=1)
            db.session.commit();db.session.remove()
        waiting=self.member.get(f'/api/evaluation-runs/{self.second_id}').get_json()
        self.assertEqual(waiting['dispatch']['state'],'waiting_worker')
        recovered=self.claim().get_json()
        self.assertEqual(recovered['id'],self.first_id)
        self.assertNotEqual(first['lease_token'],recovered['lease_token'])
        stale=self.app.test_client().post(f'/api/evaluation-worker/runs/{self.first_id}/heartbeat',headers={'X-Evaluation-Lease':first['lease_token']})
        self.assertEqual(stale.status_code,403)
        self.complete(recovered)
        self.assertEqual(self.claim().get_json()['id'],self.second_id)
        with self.app.app_context():
            self.assertEqual(db.session.get(EvaluationRun,self.first_id).submission_version.submission.evaluation_runs_used,1)

    def test_waiting_time_does_not_start_execution_clock(self):
        self.prepare_two_tracks()
        with self.app.app_context():
            job=db.session.get(EvaluationRun,self.first_id)
            job.created_at=utcnow()-timedelta(hours=2)
            db.session.commit()
        job=self.claim().get_json()
        self.assertEqual(job['id'],self.first_id)
        self.assertEqual(job['config']['resources']['time_seconds'],60)
        with self.app.app_context():
            record=db.session.get(EvaluationRun,self.first_id)
            self.assertGreater(record.started_at,record.created_at+timedelta(hours=1))

    def test_failed_task_releases_slot_for_next_team(self):
        self.prepare_two_tracks()
        first = self.claim().get_json()
        response = self.app.test_client().post(
            f"/api/evaluation-worker/runs/{first['id']}/complete",
            headers={'X-Evaluation-Lease': first['lease_token']},
            json={'status': 'failed', 'error': 'evaluation time limit exceeded'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['status'], 'failed')
        self.assertEqual(self.claim().get_json()['id'], self.second_id)

    def test_memory_admission_includes_environment_and_keeps_large_job_queued(self):
        self.prepare_two_tracks()
        with self.app.app_context():
            run = db.session.get(EvaluationRun, self.first_id)
            run.config_snapshot = {**run.config_snapshot, 'task': 'open-world'}
            problem = db.session.get(Problem, run.problem_id)
            problem.evaluation_config = {**problem.evaluation_config, 'task': 'open-world'}
            db.session.commit()
        self.caps['memory_mb'] = 4096
        second = self.claim().get_json()
        self.assertEqual(second['id'], self.second_id)
        first = self.member.get(f'/api/evaluation-runs/{self.first_id}').get_json()
        self.assertEqual(first['status'], 'queued')
        self.assertEqual(first['attempts'], 0)
        self.assertEqual(first['dispatch']['state'], 'waiting_worker')
        from platform_api.problem_setup import readiness
        with self.app.app_context():
            problem = db.session.get(EvaluationRun, self.first_id).problem
            checks = {item['key']: item['state'] for item in readiness(problem)['checks']}
            self.assertEqual(checks['worker'], 'blocked')
        self.complete(second)
        self.caps['memory_mb'] = 6144
        self.assertEqual(self.claim().get_json()['id'], self.first_id)

    def test_invalid_node_memory_capability_rejected(self):
        self.prepare_two_tracks()
        for value in [True, '6144', -1]:
            self.caps['memory_mb'] = value
            self.assertEqual(self.claim().status_code, 400)

    def test_running_heartbeat_keeps_queued_node_online(self):
        self.prepare_two_tracks()
        first = self.claim().get_json()
        with self.app.app_context():
            db.session.get(EvaluationWorkerState, 'docker-node-one').seen_at = utcnow() - timedelta(seconds=76)
            db.session.commit()
        response = self.app.test_client().post(
            f"/api/evaluation-worker/runs/{first['id']}/heartbeat",
            headers={'X-Evaluation-Lease': first['lease_token']}, json={},
        )
        self.assertEqual(response.status_code, 200)
        waiting = self.member.get(f'/api/evaluation-runs/{self.second_id}').get_json()
        self.assertEqual(waiting['dispatch']['state'], 'docker_queued')
        self.assertEqual(waiting['dispatch']['ahead'], 1)


class HostLockTests(unittest.TestCase):
    def test_duplicate_process_rejected_and_lock_released_on_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'worker.lock'
            with acquire_worker_lock(path):
                with self.assertRaises(SystemExit):acquire_worker_lock(path)
            with acquire_worker_lock(path):pass


if __name__=='__main__':unittest.main()
