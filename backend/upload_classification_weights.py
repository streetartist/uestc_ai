"""Upload private weights in resumable chunks; prints config.json weights_asset."""
import argparse
import getpass
import hashlib
import http.cookiejar
import json
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request


def upload(api, weights, problem, team, email, password, asset_id=None):
    parsed = urllib.parse.urlsplit(api)
    if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('API must be an HTTPS URL')
    api = api.rstrip('/')
    client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(path, method='GET', body=None):
        raw = isinstance(body, bytes)
        data = body if raw else json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(api + path, data=data, method=method,
            headers={'Content-Type': 'application/octet-stream' if raw else 'application/json'})
        try:
            with client.open(req, timeout=120) as response:
                return json.load(response) if response.status != 204 else None
        except urllib.error.HTTPError as error:
            payload = error.read(2048).decode(errors='replace')
            raise RuntimeError(f'HTTP {error.code}: {payload}') from None

    path = Path(weights)
    sha = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            sha.update(chunk)
    digest = sha.hexdigest()
    meta = {'name': path.name, 'size': path.stat().st_size, 'sha256': digest, 'team_id': team}
    if not 1 <= meta['size'] <= 2 * 1024**3:
        raise ValueError('weights must be between 1 byte and 2 GiB')
    call('/auth/login', 'POST', {'email': email, 'password': password})
    problem_info = call('/problems/' + urllib.parse.quote(problem, safe=''))
    problem = problem_info['id']
    teams = [item for item in call('/me/teams') if item['competition_id'] == problem_info['competition']['id']]
    if team:
        teams = [item for item in teams if team in (item['id'], item['name'])]
    if len(teams) != 1:
        raise ValueError('select one registered team with --team TEAM_NAME_OR_ID')
    meta['team_id'] = teams[0]['id']
    print('Team:', teams[0]['name'], flush=True)
    asset = call('/model-weights/' + asset_id) if asset_id else call(f'/problems/{problem}/model-weights', 'POST', meta)
    if asset['size'] != meta['size'] or asset['sha256'] != meta['sha256']:
        raise ValueError('resume asset does not match the local weight file')
    print('Upload ID:', asset['id'], flush=True)
    if asset['state'] != 'ready':
        done = set(asset.get('uploaded_chunks', []))
        with path.open('rb') as stream:
            for index in range(asset['chunks']):
                chunk = stream.read(asset['chunk_bytes'])
                if index not in done:
                    call(f"/model-weights/{asset['id']}/chunks/{index}", 'PUT', chunk)
                print(f"{index + 1}/{asset['chunks']} chunks", flush=True)
    asset = call(f"/model-weights/{asset['id']}/complete", 'POST', {})
    return {'weights_asset': {key: asset[key] for key in ('id', 'sha256', 'size')}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api', default='https://uestcai.top/api')
    for name in ('weights', 'problem', 'email'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--team', help='Team name or ID; inferred when this competition has one team')
    parser.add_argument('--resume', help='Upload ID printed by an interrupted upload')
    parser.add_argument('--output', default='weights-reference.json')
    args = parser.parse_args()
    result = upload(args.api, args.weights, args.problem, args.team, args.email,
                    getpass.getpass('Platform password: '), args.resume)
    Path(args.output).write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
