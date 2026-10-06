"""Real sequential Docker execution against a private, disposable API database.

Two teams queue a short LIBERO scene; duplicate claim is refused, and the
queued second job survives an API restart. No production competition/date/API
changes and no upstream model calls. This tests scheduling, not policy quality.
"""
import argparse
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import secrets
import subprocess
import threading
import time
import zipfile

from werkzeug.serving import make_server
from platform_api import create_app
from platform_api.extensions import db
from platform_api.models import EvaluationRun
from evaluation_adapters.wujie_scenes import libero_scenes
from evaluation_worker import execute
from evaluation_worker_lock import acquire_worker_lock
from build_wujie_competition import build_catalogue


def checked(response, code=200):
    assert response.status_code == code, (response.status_code, response.get_json())
    return response.get_json()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--controller-image', required=True)
    parser.add_argument('--agent-image', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output=args.output.resolve();output.mkdir(parents=True,exist_ok=False)
    images=[subprocess.check_output(['docker','image','inspect','--format','{{.Id}}',name],text=True).strip()
            for name in [args.controller_image,args.agent_image]]
    token=secrets.token_urlsafe(32)
    settings={'TESTING':True,'SQLALCHEMY_DATABASE_URI':'sqlite:///'+(output/'queue.sqlite3').as_posix(),
        'UPLOAD_FOLDER':str(output/'uploads'),'AUTO_CREATE_SCHEMA':True,'SEED_DATABASE':True,
        'INITIAL_ADMIN_PASSWORD':'QueueAcceptance123!','INITIAL_REVIEWER_PASSWORD':'QueueAcceptance123!',
        'ENFORCE_COMPETITION_DEADLINES':False,'EXPOSE_VERIFICATION_CODE':True,
        'EVALUATION_WORKER_TOKEN':token,'EVALUATION_ENABLED_ADAPTERS':'libero-agent-v1'}
    app=create_app(settings)
    server=make_server('127.0.0.1',0,app,threaded=True)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    port=server.server_port
    worker_id='docker-queue-acceptance'
    os.environ['EVALUATION_WORKER_ID']=worker_id
    caps={'worker_id':worker_id,'adapters':['libero-agent-v1'],'gpu':False,'managed_runtime':True,'api_proxy':False}
    auth={'Authorization':'Bearer '+token}
    start=time.monotonic()
    try:
        with acquire_worker_lock():
            admin,member,worker=[app.test_client() for _ in range(3)]
            checked(admin.post('/api/auth/login',json={'email':'admin@uestcai.top','password':'QueueAcceptance123!'}))
            comp=checked(admin.get('/api/competitions/paper-city-2027'));track=comp['tracks'][0]
            problem_data=deepcopy(build_catalogue()['tracks'][2]['problem'])
            problem_data['status']='published'
            config=problem_data['evaluation_config'];config['api']={'enabled':False,'max_calls':0}
            config['resources'].update(episodes=1,time_seconds=300)
            problem=checked(admin.post(f"/api/tracks/{track['id']}/problems",json=problem_data),201)
            scene=libero_scenes()[0];scene['max_steps']=10
            checked(admin.put(f"/api/manage/problems/{problem['id']}/setup",json={'evaluation_config':config,
                'runtime':{'image':images[0],'agent_image':images[1],'scenarios':[scene]}}))
            email='queue@std.uestc.edu.cn'
            challenge=checked(member.post('/api/auth/captcha',json={'email':email}))
            code=checked(member.post('/api/auth/verification-codes',json={'email':email,'captcha_id':challenge['id'],'captcha_code':challenge['debug_code']}),202)['debug_code']
            checked(member.post('/api/auth/register',json={'email':email,'name':'Private queue acceptance','password':'QueueAcceptance123!',
                'confirm_password':'QueueAcceptance123!','verification_code':code}),201)
            teams=[];runs=[]
            for i in range(2):
                team=checked(member.post('/api/teams',json={'competition_id':comp['id'],'name':f'Queue {i+1}'}),201)
                teams.append(team)
                checked(member.post('/api/registrations',json={'competition_id':comp['id'],'track_id':track['id'],'team_id':team['id']}),201)
                data=io.BytesIO()
                with zipfile.ZipFile(data,'w') as archive:
                    archive.writestr('agent.py','class Agent:\n    def reset(self,goal):pass\n    def act(self,observation):return [0,0,0,0,0,0,1]\n')
                    archive.writestr('config.json','{}')
                asset=checked(member.post('/api/submission-assets/stage',data={'file':(io.BytesIO(data.getvalue()),'agent.zip')}),201)
                runs.append(checked(member.post(f"/api/problems/{problem['id']}/evaluation-runs",json={'team_id':team['id'],'staged_asset_ids':[asset['id']]}),201))
            first=checked(worker.post('/api/evaluation-worker/claim',headers=auth,json=caps))
            assert first['id']==runs[0]['id']
            assert worker.post('/api/evaluation-worker/claim',headers=auth,json=caps).status_code==204
            waiting=checked(member.get('/api/evaluation-runs/'+runs[1]['id']))
            assert waiting['status']=='queued' and waiting['attempts']==0 and waiting['started_at'] is None
            assert waiting['dispatch']['ahead']==1
            print('Two teams queued; busy node refused duplicate claim; running first real LIBERO scene',flush=True)
            completed=[]
            result=execute(f'http://127.0.0.1:{port}/api',first,{},'','','',log_directory=str(output/'first'))
            assert result['status']=='completed',result
            completed.append(checked(worker.post(f"/api/evaluation-worker/runs/{first['id']}/complete",headers={'X-Evaluation-Lease':first['lease_token']},json=result)))
            # Restart the API on the same persisted DB and port before claiming B.
            server.shutdown();server.server_close();thread.join()
            with app.app_context():db.session.remove();db.engine.dispose()
            app=create_app({**settings,'AUTO_CREATE_SCHEMA':False,'SEED_DATABASE':False})
            server=make_server('127.0.0.1',port,app,threaded=True)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            worker=app.test_client()
            second=checked(worker.post('/api/evaluation-worker/claim',headers=auth,json=caps))
            assert second['id']==runs[1]['id']
            print('API restarted; original queued second task restored and claimed',flush=True)
            result=execute(f'http://127.0.0.1:{port}/api',second,{},'','','',log_directory=str(output/'second'))
            assert result['status']=='completed',result
            completed.append(checked(worker.post(f"/api/evaluation-worker/runs/{second['id']}/complete",headers={'X-Evaluation-Lease':second['lease_token']},json=result)))
            with app.app_context():
                a,b=[db.session.get(EvaluationRun,r['id']) for r in runs]
                assert b.started_at>=a.finished_at
                assert all(r.attempts==1 and r.submission_version.submission.evaluation_runs_used==1 for r in [a,b])
            assert all(len(r['artifacts'])==2 for r in completed)
            for run in runs:
                assert not subprocess.check_output(['docker','ps','-aq','--filter','label=uestc.evaluation_run='+run['id']],text=True).strip()
            proof={'status':'passed','real_docker':True,'real_libero':True,'synthetic_queue_fixture':True,
                'production_competition_modified':False,'test_steps_per_task':10,'upstream_model_tested':False,
                'same_node_serial':True,'duplicate_claim_rejected':True,'queued_attempts_before_start':0,
                'queue_survived_api_restart':True,'both_attempts':1,'used_runs_per_team':1,
                'private_evidence_per_task':2,'containers_cleaned':True,'seconds':round(time.monotonic()-start,2)}
            (output/'result.json').write_text(json.dumps(proof,indent=2))
            print(json.dumps(proof),flush=True)
    finally:
        server.shutdown();server.server_close();thread.join(timeout=2)


if __name__=='__main__':main()
