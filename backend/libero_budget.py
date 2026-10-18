"""Settle a normal LIBERO budget end from organizer-owned progress and traces."""
from copy import deepcopy
import json


def budget_episodes(job, progress, output):
    if "automatic_score" not in job['config'].get('metrics', []):
        return None
    scenes=job['runtime']['scenarios']
    episodes=deepcopy(progress['episodes'])
    if len(episodes)>len(scenes):raise ValueError('LIBERO progress exceeds frozen scene count')
    for index in range(len(episodes),len(scenes)):
        values={key:0 for key in job['config']['metrics']}
        if index==len(progress['episodes']):
            evidence=output/'evidence'/str(index+1)
            trace_path=evidence/'trajectory.json'
            trace=json.loads(trace_path.read_text()) if trace_path.exists() else {}
            rows=trace.get('steps',[])
            score=trace.get('score')
            if rows and score is None:score=rows[-1].get('score')
            if rows and score is None:
                raise ValueError('Interrupted LIBERO actions have no trusted score checkpoint')
            if score:
                values.update({key:score[key] for key in ('completion_score','efficiency_score','automatic_score')})
            values.update(action_count=len(rows),task_success=100 if rows and rows[-1].get('success') else 0,
                          invalid_actions=sum(not r.get('valid',True) for r in rows),
                          execution_seconds=trace.get('execution_seconds',sum(
                              sum(r.get(k,0) for k in ('policy_ms','simulation_ms','controller_ms'))/1000 for r in rows)))
        episodes.append({'scenario':{key:scenes[index][key] for key in ('id','label','difficulty')},
                         'metrics':{key:values[key] for key in job['config']['metrics']}})
    return episodes
