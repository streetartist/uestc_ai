"""Student scripts run with Python 3.10 and the standard library alone."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from upload_classification_weights import upload
from prepare_classification_submission import build
from check_classification_submission import check


class StudentToolsTests(unittest.TestCase):
    def test_upload_hash_package_and_local_check_on_python310(self):
        payload = b'student-model' * 100000
        identity = '12345678-1234-1234-1234-123456789abc'
        metadata = {}
        chunks = []
        class Opener:
            def open(inner, request, timeout=None):
                path = urlsplit(request.full_url).path
                status = 200
                if path.endswith('/auth/login'):
                    value = {'user': {'id': 'test'}}
                elif path.endswith('/problems/task-a'):
                    value = {'id': 'problem', 'competition': {'id': 'competition'}}
                elif path.endswith('/me/teams'):
                    value = [{'id': 'team', 'name': 'Test Team', 'competition_id': 'competition'}]
                elif path.endswith('/problems/problem/model-weights'):
                    metadata.update(json.loads(request.data))
                    value = {'id': identity, 'state': 'uploading', 'chunks': 1, 'chunk_bytes': 16 * 1024**2,
                             'size': metadata['size'], 'sha256': metadata['sha256']}
                    status = 201
                elif path.endswith('/chunks/0'):
                    chunks.append(request.data)
                    value, status = None, 204
                elif path.endswith('/complete'):
                    value = {'id': identity, 'size': metadata['size'], 'sha256': metadata['sha256'], 'state': 'ready'}
                else:
                    raise AssertionError(path)
                result = io.BytesIO(json.dumps(value).encode())
                result.status = status
                return result
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            weights = root / 'baseline.pt'
            weights.write_bytes(payload)
            with patch('urllib.request.build_opener', return_value=Opener()):
                reference = upload('https://fixture.invalid/api', weights, 'task-a', None,
                                   'fixture@example.org', 'fixture-only-password')
            self.assertEqual(chunks, [payload])
            self.assertEqual(reference['weights_asset']['sha256'], hashlib.sha256(payload).hexdigest())
            (root / 'weights-reference.json').write_text(json.dumps(reference))
            (root / 'config.json').write_text(json.dumps({'num_classes': 40, 'weights_file': 'baseline.pt'}))
            (root / 'inference.py').write_text('class Model:\n def predict(self,path): return [0]*40\n')
            result = build(root, root/'weights-reference.json', root/'inference.zip')
            self.assertEqual(result['status'], 'passed')
            self.assertTrue(check(root/'inference.zip', weights)['local_weight_hash_checked'])
            weights.write_bytes(b'changed')
            with self.assertRaises(ValueError):
                check(root/'inference.zip', weights)


if __name__ == '__main__':
    unittest.main()
