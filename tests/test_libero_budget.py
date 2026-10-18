import json,sys,tempfile,unittest
from pathlib import Path
from copy import deepcopy
from unittest.mock import patch,MagicMock
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'backend'),str(Path(__file__).parent)]
from libero_budget import budget_episodes
import test_libero_final_evaluation as final_fixtures
from platform_api.models import EvaluationRun,SubmissionVersion
from platform_api.extensions import db
from platform_api.evaluation import adapters
from evaluation_adapters.wujie_scenes import libero_scored_scenes

METRICS=['task_success','action_count','execution_seconds','invalid_actions','completion_score','efficiency_score','automatic_score']
def readings(score):
    return dict(task_success=100 if score==100 else 0,action_count=200,execution_seconds=40,invalid_actions=0,
                completion_score=60 if score==100 else 30,efficiency_score=40 if score==100 else 20,automatic_score=score)

class BudgetTraceTests(unittest.TestCase):
    def test_keep_completed_current_partial_and_zero_future(self):
        scenes=libero_scored_scenes();job={'config':{'metrics':METRICS},'runtime':{'scenarios':scenes}}
        progress={'episodes':[{'scenario':{k:scenes[0][k] for k in ('id','label','difficulty')},'metrics':readings(100)}]}
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);folder=root/'evidence/2';folder.mkdir(parents=True)
            (folder/'trajectory.json').write_text(json.dumps({'steps':[{'success':False,'valid':True}],
                'score':{'completion_score':0,'efficiency_score':0,'automatic_score':0},'execution_seconds':7}))
            result=budget_episodes(job,progress,root)
        self.assertEqual(len(result),3);self.assertEqual(result[0],progress['episodes'][0])
        self.assertEqual(result[1]['metrics']['action_count'],1);self.assertEqual(result[2]['metrics']['automatic_score'],0)

class BudgetApiTests(unittest.TestCase):
    setUp=final_fixtures.LiberoFinalTests.setUp;tearDown=final_fixtures.LiberoFinalTests.tearDown
    login=final_fixtures.LiberoFinalTests.login;register=final_fixtures.LiberoFinalTests.register;base_fixture=final_fixtures.LiberoFinalTests.base_fixture
    fixture=final_fixtures.LiberoFinalTests.fixture;stage=final_fixtures.LiberoFinalTests.stage;claim=final_fixtures.LiberoFinalTests.claim
    def prepare(self,quota=False):
        self.fixture();scenes=libero_scored_scenes()
        scenes += [{**scenes[2],'id':'hidden-'+str(i),'init_state_id':40+i} for i in range(2)]
        with self.app.app_context():
            run=db.session.get(EvaluationRun,self.trial_id);version=db.session.get(SubmissionVersion,self.version_id)
            run.submission_version_id=version.id;run.purpose='final';run.status='queued';run.metrics={};run.episodes=[]
            config=deepcopy(run.config_snapshot);config['metrics']=METRICS;config['resources']['episodes']=5
            config['metric_definitions']=[m for m in adapters()['libero-agent-v1']['metrics'] if m['key'] in METRICS]
            run.config_snapshot=config;run.runtime_snapshot={**self.runtime_config,'scenarios':scenes}
            run.submission_snapshot={**run.submission_snapshot,'captured_at':version.snapshot['captured_at'],
                'performance_scoring':{'preset':'wujie-libero-v2','criterion':'表现'},'episode_weights':[10,10,10,15,15],
                'api_call_budget_exhausted':False}
            if quota:
                config=deepcopy(run.config_snapshot);config['api']={'enabled':True,'max_calls':1};run.config_snapshot=config
                run.submission_snapshot={**run.submission_snapshot,'final_api_policy':{'allowed_models':['test-model'],
                    'max_output_tokens':4096,'reasoning_efforts':['low'],'max_request_bytes':65536,
                    'max_response_bytes':1048576,'timeout_seconds':30}}
                self.app.config.update(EVALUATION_API_URL='https://example.invalid/v1/chat/completions',EVALUATION_API_KEY='test-only')
            version.snapshot={**version.snapshot,'final_evaluation_run_id':run.id};db.session.commit()
        job=self.claim()
        episodes=[{'scenario':{k:s[k] for k in ('id','label','difficulty')},'metrics':readings(100 if i<2 else 50)}
                  for i,s in enumerate(scenes)]
        for e in episodes[3:]:e['metrics']={key:0 for key in METRICS}
        partial={'episodes':episodes[:2],'planned_episodes':5,'interrupted_scene':episodes[2]['scenario'],'reason':'execution_error'}
        return job,episodes,partial
    def test_normal_deadline_is_scored_and_publishable(self):
        job,episodes,partial=self.prepare();partial['reason']='evaluation_time_limit'
        r=self.worker.post(f'/api/evaluation-worker/runs/{job["id"]}/complete',headers={'X-Evaluation-Lease':job['lease_token']},
            json={'status':'failed','partial_results':partial,'budget_episodes':episodes})
        self.assertEqual(r.status_code,200,r.get_json());self.assertEqual(r.get_json()['status'],'completed')
        self.assertAlmostEqual(r.get_json()['performance']['score'],125/3)
        self.assertEqual(r.get_json()['budget_termination_reason'],'evaluation_time_limit')
        self.assertEqual(self.admin.post(f'/api/evaluation-runs/{job["id"]}/publish').status_code,200)
    def test_code_crash_is_not_automatically_scored(self):
        job,episodes,partial=self.prepare()
        r=self.worker.post(f'/api/evaluation-worker/runs/{job["id"]}/complete',headers={'X-Evaluation-Lease':job['lease_token']},
            json={'status':'failed','partial_results':partial,'budget_episodes':episodes})
        self.assertEqual(r.get_json()['status'],'failed');self.assertIsNone(r.get_json()['performance'])
    def test_authoritative_api_limit_is_scored(self):
        job,episodes,partial=self.prepare(quota=True)
        upstream=MagicMock();upstream.open.return_value.__enter__.return_value.status=200
        upstream.open.return_value.__enter__.return_value.read.return_value=b'{"choices":[]}'
        body={'model':'test-model','messages':[{'role':'user','content':'JSON please'}],'max_tokens':16}
        url=f'/api/evaluation-worker/runs/{job["id"]}/api';headers={'X-Evaluation-API-Token':job['api_token']}
        with patch('platform_api.routes.evaluations.urllib.request.build_opener',return_value=upstream):
            self.assertEqual(self.worker.post(url,json=body,headers=headers).status_code,200)
            self.assertEqual(self.worker.post(url,json=body,headers=headers).status_code,429)
        r=self.worker.post(f'/api/evaluation-worker/runs/{job["id"]}/complete',headers={'X-Evaluation-Lease':job['lease_token']},
            json={'status':'failed','partial_results':partial,'budget_episodes':episodes})
        self.assertEqual(r.get_json()['status'],'completed');self.assertEqual(r.get_json()['budget_termination_reason'],'api_call_limit')
