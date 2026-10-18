"""Apply this LIBERO release to the existing C problem, preserving teams and history."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path


def activate(app, document, snapshot_path=None, reset_trials=False):
    from platform_api.extensions import db
    from platform_api.models import EvaluationRun, Problem, ProblemRuntime, Submission
    from platform_api.evaluation import validate_evaluation_config
    from platform_api.problem_setup import validate_runtime
    from platform_api.performance_scoring import validate_problem_scoring
    with app.app_context():
        problem = Problem.query.filter_by(slug='wujie-robot-arm-agent').one()
        if EvaluationRun.query.filter(EvaluationRun.problem_id == problem.id,
                                      EvaluationRun.status.in_(['queued','running'])).first():
            raise ValueError('C题仍有排队或运行中的测评，请在本轮结束后切换。')
        if EvaluationRun.query.filter_by(problem_id=problem.id,purpose='final').first():
            raise ValueError('本题已有终评记录，请保留本轮配置并另行安排新版本。')
        config=validate_evaluation_config(document['evaluation_config'])
        runtime=validate_runtime(document['runtime'],config)
        scoring=deepcopy(document['scoring_config']);judging=deepcopy(document['judging_schema'])
        validate_problem_scoring(scoring,config,judging)
        old={key:deepcopy(getattr(problem,key)) for key in
             ('statement_md','summary','compute_note','evaluation_config','scoring_config','judging_schema')}
        old['runtime']=deepcopy(problem.runtime.config) if problem.runtime else None
        submissions=Submission.query.filter_by(problem_id=problem.id).all()
        old['trial_counts']=[{'submission_id':s.id,'team_id':s.team_id,'used':s.evaluation_runs_used} for s in submissions]
        preview={'problem_id':problem.id,'slug':problem.slug,'before':old,'after':document}
        if snapshot_path is None:return preview
        snapshot_path=Path(snapshot_path)
        snapshot_path.parent.mkdir(parents=True,exist_ok=True)
        with snapshot_path.open('x',encoding='utf8') as file:
            json.dump(preview,file,ensure_ascii=False,indent=2)
        for key in ('statement_md','summary','compute_note','evaluation_config','scoring_config','judging_schema'):
            setattr(problem,key,deepcopy(document[key]))
        if problem.runtime:problem.runtime.config=runtime
        else:db.session.add(ProblemRuntime(problem=problem,config=runtime))
        if reset_trials:
            for submission in submissions:submission.evaluation_runs_used=0
        from platform_api.models import AuditLog
        db.session.add(AuditLog(action='libero.release_activated',entity_type='problem',entity_id=problem.id,
                               details={'rules_version':'libero-five-20261018','snapshot':str(snapshot_path)}))
        db.session.commit()
        return {'problem_id':problem.id,'updated':True,'snapshot':str(snapshot_path)}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--snapshot',type=Path,help='Write the old configuration here and apply; omit to preview')
    parser.add_argument('--reset-trials',action='store_true',help='Refresh C trial allowance for all existing teams; keep history')
    args=parser.parse_args()
    from platform_api import create_app
    result=activate(create_app(),json.loads(args.config.read_text(encoding='utf8')),args.snapshot,args.reset_trials)
    print(json.dumps(result if args.snapshot else {k:result[k] for k in ('problem_id','slug')},ensure_ascii=False))
    if not args.snapshot:print('预览完成；指定 --snapshot <新文件.json> 应用配置。')
