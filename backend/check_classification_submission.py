"""Check a submission ZIP locally without consuming a platform trial."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

from evaluation_adapters.classification_runner import extract_package


def check(package, weights=None, sample=None):
    path = Path(package)
    if not path.is_file() or path.stat().st_size > 20 * 1024 * 1024:
        raise ValueError('code ZIP must exist and be at most 20 MiB')
    model_executed = False
    with tempfile.TemporaryDirectory(prefix='classification-check-') as temporary:
        root = Path(temporary)
        config = extract_package(path, root)
        tree = ast.parse((root / 'inference.py').read_text(encoding='utf-8'))
        models = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'Model']
        if not models or not any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == 'predict' for n in models[0].body):
            raise ValueError('inference.py must define Model.predict(sample_path)')
        sources = [key for key in ('weights_asset', 'weights', 'weights_file') if key in config]
        if len(sources) != 1 or 'weights_path' in config:
            raise ValueError('declare exactly one weight source: weights_asset, weights or weights_file')
        source = sources[0]
        ref = config[source]
        if source == 'weights_file':
            if not isinstance(ref, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,120}', ref) or not (root / ref).is_file():
                raise ValueError('bundled weights_file must exist at ZIP root')
        else:
            fields = {'id', 'sha256', 'size'} if source == 'weights_asset' else {'url', 'sha256', 'size'}
            if (not isinstance(ref, dict) or set(ref) != fields or not isinstance(ref['sha256'], str)
                    or not re.fullmatch(r'[a-f0-9]{64}', ref['sha256']) or type(ref['size']) is not int
                    or not 1 <= ref['size'] <= 2 * 1024**3):
                raise ValueError('invalid weight reference')
            if source == 'weights_asset':
                if not isinstance(ref['id'], str) or not re.fullmatch(r'[a-f0-9-]{36}', ref['id']):
                    raise ValueError('invalid weight asset ID')
            else:
                url = urlsplit(ref['url'])
                if (url.scheme != 'https' or not url.hostname or url.username or url.password
                        or url.port not in (None, 443) or url.fragment):
                    raise ValueError('external weights require an HTTPS URL')
            if weights:
                local = Path(weights)
                sha = hashlib.sha256()
                with local.open('rb') as stream:
                    while chunk := stream.read(1024 * 1024):
                        sha.update(chunk)
                digest = sha.hexdigest()
                if local.stat().st_size != ref['size'] or digest != ref['sha256']:
                    raise ValueError('local weights differ from submitted reference')
        if sample is not None:
            sample = Path(sample).resolve()
            if not sample.exists():
                raise ValueError('sample must be an existing validation clip')
            local = root / ref if source == 'weights_file' else Path(weights).resolve() if weights else None
            if local is None or not local.is_file():
                raise ValueError('--sample requires --weights for an external or uploaded weight reference')
            code = '''import sys,json,math,torch
sys.path.insert(0,sys.argv[1])
from inference import Model
with open(sys.argv[1]+'/config.json',encoding='utf-8') as f: c=json.load(f)
c.update(weights_path=sys.argv[2],package_dir=sys.argv[1])
torch.set_num_threads(4)
s=Model(c,'cuda' if torch.cuda.is_available() else 'cpu').predict(sys.argv[3])
assert len(s)==40 and all(math.isfinite(float(x)) for x in s), 'Expected 40 finite scores'
print('sample prediction passed')
'''
            result = subprocess.run([sys.executable, '-I', '-c', code, str(root), str(local), str(sample)],
                                    cwd=root, capture_output=True, text=True, timeout=180)
            if result.returncode:
                raise ValueError('Sample prediction failed: ' + (result.stderr or result.stdout)[-3000:])
            model_executed = True
    return {'status': 'passed', 'package_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'weight_source': source, 'local_weight_hash_checked': bool(weights and source != 'weights_file'),
            'checks': ['ZIP limits and safe paths', '40-class config', 'Model.predict declaration', 'weight reference'],
            'model_executed': model_executed, 'platform_trial_consumed': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('package')
    parser.add_argument('--weights', help='Local weight file to compare with the submitted reference')
    parser.add_argument('--sample', help='Run the extracted inference ZIP on one local validation clip')
    args = parser.parse_args()
    print(json.dumps(check(args.package, args.weights, args.sample), indent=2))


if __name__ == '__main__':
    main()
