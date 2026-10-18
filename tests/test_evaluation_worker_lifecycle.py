import os
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import evaluation_worker as worker


class WorkerLifecycleTests(unittest.TestCase):
    def test_controller_deadline_remains_a_time_limit_in_worker_recovery(self):
        progress = {'episodes': [], 'planned_episodes': 1,
                    'interrupted_scene': None, 'reason': 'evaluation_time_limit'}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'partial-results.json').write_text(json.dumps(progress))
            job = {'config': {'resources': {'episodes': 1}}}
            result = worker.recover_libero_progress('https://example.test', job, root, 'execution_error')
            self.assertEqual(result['reason'], 'evaluation_time_limit')
            self.assertEqual(json.loads((root / 'partial-results.json').read_text())['reason'], 'evaluation_time_limit')
            progress['reason'] = 'TimeoutError'
            (root / 'partial-results.json').write_text(json.dumps(progress))
            early = worker.recover_libero_progress('https://example.test', job, root, 'execution_error')
            self.assertEqual(early['reason'], 'execution_error')

    def test_libero_timeout_retains_completed_scenes_and_interrupted_evidence(self):
        completed = {'scenario': {'id': 'bowl', 'label': 'Bowl', 'difficulty': 'beginner'},
                     'metrics': {'task_success': 100}}
        partial = {'episodes': [completed], 'planned_episodes': 2,
                   'interrupted_scene': {'id': 'basket', 'label': 'Basket', 'difficulty': 'intermediate'},
                   'reason': None}
        uploaded, commands = [], []
        elapsed = [0.0]

        def mount(command, destination):
            value = next(command[index + 1] for index, option in enumerate(command[:-1])
                         if option == '--mount' and f'dst={destination}' in command[index + 1])
            return Path(value.split('src=', 1)[1].split(',dst=', 1)[0])

        def launch(command, **kwargs):
            output = mount(command, '/output')
            (output / 'partial-results.json').write_text(json.dumps(partial))
            for name in ('trajectory.json', 'replay.gif', 'replay.mp4'):
                (output / 'evidence' / '1' / name).write_bytes(b'completed evidence')
            (output / 'evidence' / '2' / 'trajectory-steps.jsonl').write_text('{"step":1}\n{"step":')
            if any('type=bind' in part and 'dst=/ipc' in part for part in command):
                (mount(command, '/ipc') / 'agent.sock').touch()
            return SimpleNamespace(returncode=0, poll=lambda: 0, wait=lambda **kwargs: 0)

        def docker(command, **kwargs):
            commands.append(command)
            if command[:2] == ['docker', 'run']:
                elapsed[0] = 61.0
                raise worker.subprocess.TimeoutExpired(command, kwargs['timeout'])
            return SimpleNamespace(returncode=0)

        job = {'id': 'partial-run', 'lease_token': 'lease', 'asset_url': '/asset.zip',
               'config': {'adapter': 'libero-agent-v1', 'task': 'manipulation',
                          'resources': {'cpus': 2, 'memory_mb': 2048, 'gpu': False,
                                        'time_seconds': 60, 'episodes': 2},
                          'api': {'enabled': False, 'max_calls': 0}}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scenes = root / 'scenes.json'; scenes.write_text('[]')
            logs = root / 'retained'
            with patch.object(worker, 'download', side_effect=lambda b, a, l, p: p.write_bytes(b'zip')), \
                    patch.object(worker, 'heartbeat'), patch.object(worker.subprocess, 'Popen', side_effect=launch), \
                    patch.object(worker.subprocess, 'run', side_effect=docker), \
                    patch.object(worker.time, 'monotonic', side_effect=lambda: elapsed[0]), \
                    patch.object(worker, 'upload_evidence_file', side_effect=lambda b, j, n, p: uploaded.append(n)):
                result = worker.execute_isolated_agent('https://example.test/api', job,
                    'sha256:' + 'a'*64, 'sha256:' + 'b'*64, str(scenes), '', '', str(logs))
            expected = {**partial, 'reason': 'evaluation_time_limit'}
            self.assertEqual(result, {'status': 'failed', 'error': 'evaluation time limit exceeded', 'partial_results': expected})
            self.assertEqual(json.loads((logs / 'partial-results.json').read_text()), expected)
            self.assertEqual(json.loads((logs / 'evidence' / '2' / 'trajectory.json').read_text())['steps'], [{'step': 1}])
            self.assertEqual((logs / 'evidence' / '1' / 'replay.mp4').read_bytes(), b'completed evidence')
        self.assertIn('scene-1-replay.mp4', uploaded)
        self.assertIn('scene-2-trajectory.json', uploaded)
        self.assertTrue(any(command[:4] == ['docker', 'stop', '--time', '25'] for command in commands))
        self.assertEqual(sum(command[:3] == ['docker', 'rm', '-f'] for command in commands), 2)

    def exercise_evidence_upload(self, failure_name, error):
        episodes = [{'scenario': {'id': 'bowl', 'label': 'Bowl', 'difficulty': 'beginner'},
                     'metrics': {'task_success': 100, 'action_count': 503}}]
        uploaded = []

        def mount(command, destination):
            value = next(command[index + 1] for index, option in enumerate(command[:-1])
                         if option == '--mount' and f'dst={destination}' in command[index + 1])
            return Path(value.split('src=', 1)[1].split(',dst=', 1)[0])

        def launch(command, **kwargs):
            output = mount(command, '/output')
            (output / 'result.json').write_text(json.dumps({'episodes': episodes}))
            for name in ('replay.gif', 'trajectory.json', 'replay.mp4'):
                (output / 'evidence' / '1' / name).write_bytes(b'evidence fixture')
            if any('type=bind' in part and 'dst=/ipc' in part for part in command):
                (mount(command, '/ipc') / 'agent.sock').touch()
            return SimpleNamespace(returncode=0, poll=lambda: 0, wait=lambda **kwargs: 0)

        def upload(base, job, name, path):
            uploaded.append(name)
            if name.endswith(failure_name):
                raise error

        job = {'id': 'optional-video', 'lease_token': 'private-lease', 'asset_url': '/asset.zip',
               'config': {'adapter': 'libero-agent-v1', 'task': 'manipulation',
                          'resources': {'cpus': 2, 'memory_mb': 2048, 'gpu': False,
                                        'time_seconds': 60, 'episodes': 1},
                          'api': {'enabled': False, 'max_calls': 0}}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scenes = root / 'scenes.json'
            scenes.write_text('[]')
            logs = root / 'retained'
            with patch.object(worker, 'download', side_effect=lambda base, asset, lease, destination: destination.write_bytes(b'zip fixture')), \
                    patch.object(worker, 'heartbeat'), \
                    patch.object(worker.subprocess, 'Popen', side_effect=launch), \
                    patch.object(worker.subprocess, 'run', return_value=SimpleNamespace(returncode=0)), \
                    patch.object(worker, 'upload_evidence_file', side_effect=upload):
                result = worker.execute_isolated_agent('https://example.test/api', job,
                    'sha256:' + 'a'*64, 'sha256:' + 'b'*64, str(scenes), '', '', str(logs))
            self.assertEqual(result, {'status': 'completed', 'episodes': episodes})
            self.assertEqual(uploaded, ['scene-1-replay.gif', 'scene-1-trajectory.json', 'scene-1-replay.mp4'])
            self.assertEqual((logs / 'evidence' / '1' / 'replay.mp4').read_bytes(), b'evidence fixture')

    def test_optional_mp4_upload_failure_preserves_scores_and_retains_video(self):
        for error in (OSError('connection interrupted'), ValueError('trusted evidence exceeds 25 MB')):
            with self.subTest(error=type(error).__name__):
                self.exercise_evidence_upload('replay.mp4', error)

    def test_required_gif_and_trajectory_upload_failures_keep_existing_behavior(self):
        for name in ('replay.gif', 'trajectory.json'):
            with self.subTest(name=name), self.assertRaisesRegex(OSError, 'required evidence unavailable'):
                self.exercise_evidence_upload(name, OSError('required evidence unavailable'))

    def assert_agent_termination(self, adapter, phase):
        """Exercise phase boundaries without waiting for a real minute in each case."""
        clock = [100.0]
        removed, timeouts, roots = [], [], []
        controller = SimpleNamespace(returncode=None, poll=lambda: None)
        waits = []

        def mount(command, destination):
            value = next(command[index + 1] for index, option in enumerate(command[:-1])
                         if option == '--mount' and f'dst={destination}' in command[index + 1])
            return Path(value.split('src=', 1)[1].split(',dst=', 1)[0])

        def launch(command, **kwargs):
            roots.append(mount(command, '/output').parent)
            clock[0] += 61 if phase == 'startup' else 15
            if phase != 'startup' and any('type=bind' in part and 'dst=/ipc' in part for part in command):
                (mount(command, '/ipc') / 'agent.sock').touch()
            return controller

        def wait(timeout):
            waits.append(timeout)
            if phase == 'controller' and len(waits) == 1:
                self.assertEqual(timeout, 25)
                clock[0] += timeout
                raise worker.subprocess.TimeoutExpired('controller', timeout)
            controller.returncode = 0
            return 0

        controller.wait = wait

        def docker(command, **kwargs):
            if command[:3] == ['docker', 'rm', '-f']:
                removed.append(command[-1])
            elif command[:2] == ['docker', 'run']:
                timeouts.append(kwargs['timeout'])
                self.assertEqual(kwargs['timeout'], 45, 'Environment startup must consume the same overall budget')
                if phase == 'shutdown':
                    worker.stop_worker(None, None)
                if phase == 'agent':
                    clock[0] += kwargs['timeout']
                    raise worker.subprocess.TimeoutExpired(command, kwargs['timeout'])
                clock[0] += 20
            return SimpleNamespace(returncode=0)

        job = {'id': 'phase-' + phase, 'lease_token': 'private-lease', 'asset_url': '/asset.zip',
               'config': {'adapter': adapter, 'task': 'open-world' if adapter == 'minecraft-agent-v1' else 'manipulation',
                          'resources': {'cpus': 2, 'memory_mb': 2048, 'gpu': False,
                                        'time_seconds': 60, 'episodes': 3},
                          'api': {'enabled': False, 'max_calls': 0}}}
        with tempfile.TemporaryDirectory() as directory:
            scenes = Path(directory) / 'scenes.json'
            scenes.write_text(json.dumps([]))
            with patch.object(worker, 'download', side_effect=lambda base, asset, lease, destination: destination.write_bytes(b'zip fixture')), \
                    patch.object(worker, 'heartbeat'), \
                    patch.object(worker.time, 'monotonic', side_effect=lambda: clock[0]), \
                    patch.object(worker.subprocess, 'Popen', side_effect=launch), \
                    patch.object(worker.subprocess, 'run', side_effect=docker):
                with self.assertRaises(SystemExit if phase == 'shutdown' else worker.subprocess.TimeoutExpired):
                    worker.execute_isolated_agent('https://example.test/api', job,
                        'sha256:' + 'a'*64, 'sha256:' + 'b'*64, str(scenes), '', '')
        self.assertEqual(len(removed), 2, 'Both contestant and simulator must be removed')
        self.assertTrue(any('-agent-' in name for name in removed))
        self.assertTrue(any('-env-' in name for name in removed))
        self.assertTrue(all(not root.exists() for root in roots), 'Private run directories must be removed')
        if phase == 'startup':
            self.assertEqual(timeouts, [])

    def test_agent_timeout_removes_both_containers_for_both_tracks(self):
        for adapter in ['minecraft-agent-v1', 'libero-agent-v1']:
            with self.subTest(adapter=adapter):
                self.assert_agent_termination(adapter, 'agent')

    def test_environment_startup_cannot_exceed_overall_budget(self):
        for adapter in ['minecraft-agent-v1', 'libero-agent-v1']:
            with self.subTest(adapter=adapter):
                self.assert_agent_termination(adapter, 'startup')

    def test_controller_wait_uses_remaining_budget_after_startup_and_agent(self):
        for adapter in ['minecraft-agent-v1', 'libero-agent-v1']:
            with self.subTest(adapter=adapter):
                self.assert_agent_termination(adapter, 'controller')

    def test_shutdown_removes_both_agent_track_containers(self):
        for adapter in ['minecraft-agent-v1', 'libero-agent-v1']:
            with self.subTest(adapter=adapter):
                self.assert_agent_termination(adapter, 'shutdown')

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
                  'EVALUATION_MEMORY_BUDGET_MB': '512',
                  'EVALUATION_INSTALLED_ADAPTERS': 'robot-arm-agent-v1,minecraft-agent-v1',
                  'EVALUATION_WORKER_ADAPTERS': '', 'EVALUATION_API_PROXY_URL': '', 'EVALUATION_NETWORK': ''}
        with tempfile.TemporaryDirectory() as directory:
            values['EVALUATION_WORKER_LOCK_FILE'] = str(Path(directory) / 'worker.lock')
            with patch.dict(os.environ, values), patch.object(sys, 'argv', ['worker', '--once']), patch.object(worker.signal, 'signal'), patch.object(worker, 'cleanup_worker_containers'), patch.object(worker.subprocess, 'run', return_value=SimpleNamespace(returncode=0)), patch.object(worker, 'request_json', side_effect=api):
                worker.main()
        self.assertEqual(claimed[0]['adapters'], ['minecraft-agent-v1', 'robot-arm-agent-v1'])
        self.assertEqual(claimed[0]['legacy_adapters'], [])
        self.assertTrue(claimed[0]['managed_runtime'])

    def test_worker_reports_time_limit_failure_to_platform(self):
        for adapter in ['minecraft-agent-v1', 'libero-agent-v1']:
            with self.subTest(adapter=adapter):
                completed = []
                job = {'id': 'time-limit-run', 'lease_token': 'private-lease', 'config': {'adapter': adapter}}

                def api(base, path, method='GET', body=None, headers=None):
                    if path.endswith('runtime-catalog'):
                        return {'adapters': [adapter]}
                    if path.endswith('/claim'):
                        return job
                    completed.append((path, body, headers))
                    return None

                values = {'EVALUATION_API_BASE': 'https://example.test/api', 'EVALUATION_WORKER_TOKEN': 'test-secret',
                          'EVALUATION_WORKER_ID': 'worker-a', 'EVALUATION_IMAGES_JSON': '{}',
                          'EVALUATION_MEMORY_BUDGET_MB': '6144', 'EVALUATION_INSTALLED_ADAPTERS': adapter,
                          'EVALUATION_WORKER_ADAPTERS': '', 'EVALUATION_API_PROXY_URL': '', 'EVALUATION_NETWORK': ''}
                with patch.dict(os.environ, values), patch.object(sys, 'argv', ['worker', '--once']), \
                        patch.object(worker.signal, 'signal'), patch.object(worker, 'cleanup_worker_containers'), \
                        patch.object(worker.subprocess, 'run', return_value=SimpleNamespace(returncode=0)), \
                        patch.object(worker, 'request_json', side_effect=api), \
                        patch.object(worker, 'execute', side_effect=worker.subprocess.TimeoutExpired('docker run', 1800)):
                    worker.run_worker()
                self.assertEqual(completed, [('/evaluation-worker/runs/time-limit-run/complete',
                    {'status': 'failed', 'error': 'evaluation time limit exceeded'}, {'X-Evaluation-Lease': 'private-lease'})])


if __name__ == '__main__': unittest.main()
