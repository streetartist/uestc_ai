import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import evaluation_worker as worker


class WorkerLifecycleTests(unittest.TestCase):
    def test_shutdown_removes_active_container_and_propagates_exit(self):
        job = {'id': 'shutdown-run', 'lease_token': 'private-lease', 'asset_url': '/asset.zip',
               'config': {'adapter': 'classification-v1', 'resources': {'cpus': 1, 'memory_mb': 512,
                          'gpu': False, 'time_seconds': 60}, 'api': {'enabled': False}}}
        removed = []
        def docker(command, **kwargs):
            if command[:3] == ['docker', 'rm', '-f']:
                removed.append(command[-1])
                return SimpleNamespace(returncode=0)
            worker.stop_worker(None, None)
        with patch.object(worker, 'download'), patch.object(worker, 'heartbeat'), patch.object(worker.subprocess, 'run', side_effect=docker):
            with self.assertRaises(SystemExit) as raised:
                worker.execute('https://example.test/api', job, {'classification-v1': 'sha256:' + 'a'*64}, '', '', '')
        self.assertEqual(raised.exception.code, 0)
        self.assertEqual(len(removed), 1)

    def test_crash_recovery_keeps_other_workers_containers(self):
        containers = {'mine': 'worker-a', 'other': 'worker-b'}
        def listed(command, **kwargs):
            owner = command[-1].split('=', 2)[-1]
            return '\n'.join(name for name, value in containers.items() if value == owner)
        def removed(command, **kwargs):
            del containers[command[-1]]
            return SimpleNamespace(returncode=0)
        with patch.object(worker.subprocess, 'check_output', side_effect=listed), patch.object(worker.subprocess, 'run', side_effect=removed):
            worker.cleanup_worker_containers('worker-a')
        self.assertEqual(containers, {'other': 'worker-b'})

    def test_installed_adapters_report_online_before_first_problem_import(self):
        claimed = []
        def api(base, path, method='GET', body=None, headers=None):
            if path.endswith('runtime-catalog'):
                return {'adapters': []}
            claimed.append(body)
            return None
        values = {'EVALUATION_API_BASE': 'https://example.test/api', 'EVALUATION_WORKER_TOKEN': 'test-secret',
                  'EVALUATION_WORKER_ID': 'worker-a', 'EVALUATION_IMAGES_JSON': '{}',
                  'EVALUATION_INSTALLED_ADAPTERS': 'robot-arm-agent-v1,minecraft-agent-v1',
                  'EVALUATION_WORKER_ADAPTERS': '', 'EVALUATION_API_PROXY_URL': '', 'EVALUATION_NETWORK': ''}
        with patch.dict(os.environ, values), patch.object(sys, 'argv', ['worker', '--once']), patch.object(worker.signal, 'signal'), patch.object(worker, 'cleanup_worker_containers'), patch.object(worker.subprocess, 'run', return_value=SimpleNamespace(returncode=0)), patch.object(worker, 'request_json', side_effect=api):
            worker.main()
        self.assertEqual(claimed[0]['adapters'], ['minecraft-agent-v1', 'robot-arm-agent-v1'])
        self.assertEqual(claimed[0]['legacy_adapters'], [])
        self.assertTrue(claimed[0]['managed_runtime'])


if __name__ == '__main__': unittest.main()
