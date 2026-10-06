"""Real Docker smoke test: team trial -> trusted metrics/evidence -> selected formal result.

Uses an isolated database and no upstream model. Quick runs shorten step budgets;
--full-steps uses the published budgets. Neither represents baseline model quality.
"""
from __future__ import annotations
import argparse
from copy import deepcopy
import io
import json
from pathlib import Path
import secrets
import subprocess
import threading
import zipfile

from werkzeug.serving import make_server
from build_wujie_competition import build_catalogue
from evaluation_adapters.wujie_scenes import minecraft_scenes, libero_scenes
from evaluation_worker import execute
from platform_api import create_app
from platform_api.extensions import db


def checked(response, status=200):
    if response.status_code != status:
        raise RuntimeError(f"HTTP {response.status_code}: {response.get_json()}")
    return response.get_json()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--adapter', choices=['minecraft', 'libero'], required=True)
    parser.add_argument('--controller-image', required=True)
    parser.add_argument('--agent-image', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--full-steps', action='store_true')
    parser.add_argument('--negative', action='store_true')
    parser.add_argument('--time-limit', type=int, default=600)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    images = [subprocess.check_output(['docker','image','inspect','--format','{{.Id}}',name], text=True).strip()
              for name in [args.controller_image,args.agent_image]]
    scene_factory = minecraft_scenes if args.adapter == 'minecraft' else libero_scenes
    scenes = scene_factory()
    planned_steps = [scene['max_steps'] for scene in scenes]
    if not args.full_steps:
        for scene in scenes: scene['max_steps'] = 8 if args.adapter == 'minecraft' else 30
    kind = 'world' if args.adapter == 'minecraft' else 'arm'
    catalogue = build_catalogue()
    problem_data = deepcopy(catalogue['tracks'][1 if kind == 'world' else 2]['problem'])
    problem_data['evaluation_config']['api'] = {'enabled':False,'max_calls':0}
    problem_data['evaluation_config']['resources']['time_seconds'] = args.time_limit
    adapter = problem_data['evaluation_config']['adapter']
    token = secrets.token_urlsafe(32)
    app = create_app({'TESTING':True, 'SQLALCHEMY_DATABASE_URI':'sqlite:///' + (output/'test.sqlite3').as_posix(),
        'UPLOAD_FOLDER':str(output/'uploads'), 'AUTO_CREATE_SCHEMA':True, 'SEED_DATABASE':True,
        'ENFORCE_COMPETITION_DEADLINES':False, 'INITIAL_ADMIN_PASSWORD':'Acceptance123!',
        'INITIAL_REVIEWER_PASSWORD':'Acceptance123!', 'EXPOSE_VERIFICATION_CODE':True,
        'EVALUATION_WORKER_TOKEN':token, 'EVALUATION_ENABLED_ADAPTERS':adapter})
    server = make_server('127.0.0.1', 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        admin, member, reviewer, worker = [app.test_client() for _ in range(4)]
        checked(admin.post('/api/auth/login', json={'email':'admin@uestcai.top','password':'Acceptance123!'}))
        competition = checked(admin.get('/api/competitions/paper-city-2027'))
        track = competition['tracks'][0]
        problem_data['status'] = 'published'
        problem = checked(admin.post(f"/api/tracks/{track['id']}/problems", json=problem_data),201)
        checked(admin.put(f"/api/manage/problems/{problem['id']}/setup", json={'evaluation_config':problem_data['evaluation_config'],
            'runtime':{'image':images[0],'agent_image':images[1],'scenarios':scenes}}))
        email = 'smoke@std.uestc.edu.cn'
        challenge = checked(member.post('/api/auth/captcha', json={'email':email}))
        code = checked(member.post('/api/auth/verification-codes', json={'email':email,'captcha_id':challenge['id'],'captcha_code':challenge['debug_code']}),202)['debug_code']
        checked(member.post('/api/auth/register', json={'email':email,'name':'Smoke','password':'Acceptance123!',
            'confirm_password':'Acceptance123!','verification_code':code}),201)
        team = checked(member.post('/api/teams', json={'competition_id':competition['id'],'name':'Smoke'}),201)
        checked(member.post('/api/registrations', json={'competition_id':competition['id'],'track_id':track['id'],'team_id':team['id']}),201)
        code_path = Path(__file__).parent/'evaluation_adapters'/('minecraft/example_agent.py' if kind=='world' else 'libero/example_agent.py')
        source = code_path.read_text()
        if args.negative:
            source = '''class Agent:
    def reset(self, goal): self.goal = goal
    def act(self, observation):
        import os
        assert not os.path.exists('/scenarios/scenarios.json')
        assert not os.path.exists('/output/result.json')
        assert 'seed' not in observation and 'object-state' not in observation
        return {'task_success':100}
'''
        package = io.BytesIO()
        with zipfile.ZipFile(package,'w') as archive:
            archive.writestr('agent.py',source); archive.writestr('config.json','{}')
        staged = checked(member.post('/api/submission-assets/stage', data={'file':(io.BytesIO(package.getvalue()),'agent.zip')}),201)
        trial = checked(member.post(f"/api/problems/{problem['id']}/evaluation-runs", json={'team_id':team['id'],'staged_asset_ids':[staged['id']]}),201)
        job = checked(worker.post('/api/evaluation-worker/claim', headers={'Authorization':'Bearer '+token},
            json={'adapters':[adapter],'gpu':False,'managed_runtime':True}))
        print(f'Real {args.adapter} started: 3 independently reset tasks', flush=True)
        result = execute(f'http://127.0.0.1:{server.server_port}/api',job,{},'','','',log_directory=str(output))
        completed = checked(worker.post(f"/api/evaluation-worker/runs/{job['id']}/complete", headers={'X-Evaluation-Lease':job['lease_token']},json=result))
        assert len(completed['artifacts']) == 6
        if args.negative:
            assert all(e['metrics']['task_success'] == 0 and e['metrics']['invalid_actions'] == s['max_steps'] for e,s in zip(completed['episodes'],scenes))
        else:
            assert all(e['metrics']['invalid_actions'] == 0 for e in completed['episodes'])
        formal = checked(member.post('/api/submissions',json={'problem_id':problem['id'],'team_id':team['id'],
            'title':'Smoke','readme_md':'# 技术路线\n接口测试，无基线质量声明。','fields':{'runtime_notes':'公开配置，真实环境'},
            'status':'submitted','staged_asset_ids':[staged['id']],'evaluation_run_id':trial['id']}),201)
        checked(reviewer.post('/api/auth/login',json={'email':'reviewer@uestc.ai','password':'Acceptance123!'}))
        visible = checked(reviewer.get(f"/api/evaluation-runs/{trial['id']}"))
        assert visible['metrics'] == completed['metrics']
        for artifact in visible['artifacts']:
            response = reviewer.get(artifact['url'], buffered=True)
            assert response.status_code == 200 and response.data
            response.close()
            assert app.test_client().get(artifact['url']).status_code == 401
        budget = checked(member.get(f"/api/problems/{problem['id']}/evaluation-budget?team_id={team['id']}"))
        assert budget['used_runs'] == 1
        assert not subprocess.check_output(['docker','ps','-aq','--filter','label=uestc.evaluation_run='+trial['id']],text=True).strip()
        report = {'status':'passed','adapter':adapter,'real_environment':True,'production_api':False,'upstream_model_tested':False,
            'controller_image':images[0],'agent_image':images[1],'published_step_budgets':planned_steps,
            'tested_step_budgets':[s['max_steps'] for s in scenes], 'negative':args.negative,
            'team_and_reviewer_result_verified':True,'formal_did_not_charge_extra':True,
            'private_evidence_verified':True,'containers_cleaned':True,'evaluation':completed}
        (output/'verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps({k:v for k,v in report.items() if k!='evaluation'},ensure_ascii=False),flush=True)
        print(json.dumps(completed['metrics'],ensure_ascii=False),flush=True)
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)
        with app.app_context(): db.session.remove(); db.engine.dispose()


if __name__ == '__main__': main()
