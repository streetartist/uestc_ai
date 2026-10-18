"""Organizer login and final evaluation start/publish commands."""
import argparse, http.cookiejar, json, os, urllib.request
from pathlib import Path

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--site',default='https://uestcai.top');p.add_argument('--email',required=True)
    p.add_argument('--password-env',default='UESTC_ORGANIZER_PASSWORD')
    mode=p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--version-id');mode.add_argument('--publish-run')
    p.add_argument('--request',type=Path,help='The same final-request.json for every team')
    args=p.parse_args();password=os.environ[args.password_env]
    session=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    def post(path,body):
        req=urllib.request.Request(args.site.rstrip('/')+path,data=json.dumps(body).encode(),
                                   headers={'Content-Type':'application/json'},method='POST')
        with session.open(req,timeout=120) as response:return json.load(response)
    post('/api/auth/login',{'email':args.email,'password':password})
    if args.version_id:
        if not args.request:p.error('--version-id requires --request')
        result=post('/api/submission-versions/'+args.version_id+'/final-evaluation-runs',
                    json.loads(args.request.read_text(encoding='utf8')))
    else:result=post('/api/evaluation-runs/'+args.publish_run+'/publish',{})
    print(json.dumps({k:result.get(k) for k in ('id','status','purpose','performance')},ensure_ascii=False))

if __name__=='__main__':main()
