import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from types import SimpleNamespace
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from evaluation_adapters.libero_runner import EvaluationInterrupted, evaluate, record_frame, run_episode
from evaluation_adapters.wujie_scenes import libero_scenes


class LiberoReplayTests(unittest.TestCase):
    def test_last_control_success_is_kept_at_deadline(self):
        from test_libero_evaluation import Channel, FakeEnv, observation
        clock = [0.0]
        class FinishingEnv(FakeEnv):
            def step(self, action):
                result = super().step(action)
                clock[0] = 10.01
                return result
        env = FinishingEnv(1)
        with tempfile.TemporaryDirectory() as directory, \
                patch('evaluation_adapters.libero_runner.time.monotonic', side_effect=lambda: clock[0]), \
                patch('evaluation_adapters.libero_runner.record_frame'), \
                patch('evaluation_adapters.libero.replay.ReplayWriter', return_value=SimpleNamespace(close=lambda: {})):
            metrics = run_episode(Channel([[0,0,0,0,0,0,1]]), env, observation(), 'task',
                libero_scenes()[0], ['task_success'], Path(directory), deadline=10)
            self.assertEqual(metrics['task_success'], 100)
            trace = json.loads((Path(directory)/'trajectory.json').read_text())
            self.assertEqual(trace['status'], 'completed')
            self.assertIn('controller_ms', trace['steps'][0])

    def test_round_deadline_is_shared_across_scene_loading_and_execution(self):
        clock = [0.0]
        scenes = libero_scenes()[:2]
        socket = MagicMock()
        listener = socket.return_value.__enter__.return_value
        connection = MagicMock()
        listener.accept.return_value = (connection, None)
        env = MagicMock()
        config = {'adapter': 'libero-agent-v1', 'task': 'manipulation', 'metrics': ['task_success'],
                  'resources': {'episodes': 2, 'time_seconds': 10}}
        def factory(scene):
            clock[0] += 4
            return env, {}, 'task'
        def episode(*args):
            self.assertEqual(args[-1], 10)
            clock[0] += 4
            return {'task_success': 100}
        with tempfile.TemporaryDirectory() as directory, \
                patch('evaluation_adapters.libero_runner.time.monotonic', side_effect=lambda: clock[0]), \
                patch('evaluation_adapters.libero_runner.socket.socket', socket), \
                patch('evaluation_adapters.libero_runner.JsonChannel'), \
                patch('evaluation_adapters.libero_runner.run_episode', side_effect=episode) as run:
            output = Path(directory) / 'result.json'
            with self.assertRaisesRegex(TimeoutError, 'evaluation time limit'):
                evaluate(config, scenes, factory, ('127.0.0.1', 1234), output)
            partial = json.loads((Path(directory) / 'partial-results.json').read_text())
            self.assertEqual(run.call_count, 1)
            self.assertEqual(len(partial['episodes']), 1)
            self.assertEqual(partial['interrupted_scene']['id'], scenes[1]['id'])
            self.assertEqual(partial['reason'], 'evaluation_time_limit')
            self.assertFalse(output.exists())
        listener.settimeout.assert_called_once_with(10)
        self.assertEqual(env.close.call_count, 2)

    def test_remaining_socket_budget_shrinks_and_late_action_is_not_executed(self):
        from test_libero_evaluation import FakeEnv, observation
        clock = [0.0]
        env = FakeEnv(999)
        connection = MagicMock()
        class Channel:
            def __init__(self): self.connection, self.ready = connection, True
            def send(self, value): pass
            def receive(self):
                if self.ready:
                    self.ready = False
                    clock[0] += 1
                    return {'type': 'ready'}
                clock[0] += 4.5
                return {'type': 'action', 'value': [0] * 7}
        original_step = env.step
        def step(action):
            clock[0] += .5
            return original_step(action)
        env.step = step
        recorder = MagicMock(error=None)
        recorder.close.return_value = {'available': False}
        with tempfile.TemporaryDirectory() as directory, \
                patch('evaluation_adapters.libero_runner.time.monotonic', side_effect=lambda: clock[0]), \
                patch('evaluation_adapters.libero_runner.record_frame'), \
                patch('evaluation_adapters.libero.replay.ReplayWriter', return_value=recorder):
            with self.assertRaisesRegex(TimeoutError, 'evaluation time limit'):
                run_episode(Channel(), env, observation(), 'task', libero_scenes()[0], ['task_success'], Path(directory), deadline=10)
            trace = json.loads((Path(directory) / 'trajectory.json').read_text())
            self.assertEqual(trace['status'], 'interrupted')
            self.assertEqual(len(trace['steps']), 1)
        self.assertEqual(env.steps, 1)
        self.assertEqual([call.args[0] for call in connection.settimeout.call_args_list], [10, 10, 9, 9, 4, 4])

    def test_termination_during_optional_rendering_is_not_swallowed(self):
        env = SimpleNamespace(sim=SimpleNamespace(render=MagicMock(side_effect=EvaluationInterrupted())))
        recorder = MagicMock(error=None)
        with self.assertRaises(EvaluationInterrupted):
            record_frame(recorder, env, {}, 256)
        recorder._failed.assert_not_called()

    def test_interrupted_action_wait_flushes_previous_actions_and_replay(self):
        from test_libero_evaluation import Channel, FakeEnv, observation
        env = FakeEnv(999)
        env.sim = SimpleNamespace(render=lambda **kw: np.zeros((kw['height'], kw['width'], 3), dtype=np.uint8))
        recorder = MagicMock(error=None)
        recorder.close.return_value = {'available': True, 'frames': 2, 'fps': 20}
        channel = Channel([[0] * 7])  # Second decision has no reply.
        with tempfile.TemporaryDirectory() as directory, patch('evaluation_adapters.libero.replay.ReplayWriter', return_value=recorder):
            with self.assertRaises(StopIteration):
                run_episode(channel, env, observation(), 'task', libero_scenes()[0], ['task_success'], Path(directory))
            trace = json.loads((Path(directory) / 'trajectory.json').read_text())
            journal = (Path(directory) / 'trajectory-steps.jsonl').read_text().splitlines()
            self.assertEqual(trace['status'], 'interrupted')
            self.assertEqual(trace['interruption'], 'StopIteration')
            self.assertEqual(len(trace['steps']), 1)
            self.assertEqual(trace['steps'][0]['success'], False)
            self.assertEqual(json.loads(journal[0]), trace['steps'][0])
            self.assertTrue((Path(directory) / 'replay.gif').is_file())
            recorder.close.assert_called_once()

    def test_second_scene_failure_keeps_first_scene_checkpoint(self):
        scenes = libero_scenes()[:2]
        socket = MagicMock()
        listener = socket.return_value.__enter__.return_value
        listener.accept.return_value = (MagicMock(), None)
        env = MagicMock()
        config = {'adapter': 'libero-agent-v1', 'task': 'manipulation', 'metrics': ['task_success'],
                  'resources': {'episodes': 2, 'time_seconds': 60}}
        with tempfile.TemporaryDirectory() as directory, \
                patch('evaluation_adapters.libero_runner.socket.socket', socket), \
                patch('evaluation_adapters.libero_runner.JsonChannel'), \
                patch('evaluation_adapters.libero_runner.run_episode', side_effect=[{'task_success': 100}, TimeoutError('waiting for action')]):
            output = Path(directory) / 'result.json'
            with self.assertRaises(TimeoutError):
                evaluate(config, scenes, lambda scene: (env, {}, 'task'), ('127.0.0.1', 1234), output)
            partial = json.loads((Path(directory) / 'partial-results.json').read_text())
            self.assertFalse(output.exists())
            self.assertEqual(partial['episodes'], [{'scenario': {key: scenes[0][key] for key in ('id', 'label', 'difficulty')},
                                                    'metrics': {'task_success': 100}}])
            self.assertEqual(partial['planned_episodes'], 2)
            self.assertEqual(partial['interrupted_scene']['id'], scenes[1]['id'])
            self.assertEqual(partial['reason'], 'TimeoutError')
            self.assertEqual(env.close.call_count, 2)

    def test_recording_resolution_does_not_change_agent_observations_or_scores(self):
        from test_libero_evaluation import Channel, FakeEnv, observation
        env = FakeEnv(2)
        rendered = []
        def render(**kwargs):
            rendered.append(kwargs)
            return np.zeros((kwargs['height'], kwargs['width'], 3), dtype=np.uint8)
        env.sim = SimpleNamespace(render=render)
        frames = []
        class Recorder:
            error = None
            def __init__(self, _): pass
            def append_observation(self, value): frames.append(value)
            def close(self): return {'available': True, 'frames': len(frames), 'fps': 20}
        scene = libero_scenes()[0]
        scene['max_steps'] = 3
        channel = Channel([[0,0,0,0,0,0,-1]] * 3)
        with tempfile.TemporaryDirectory() as directory, patch('evaluation_adapters.libero.replay.ReplayWriter', Recorder):
            result = run_episode(channel, env, observation(), 'task', scene, ['task_success', 'action_count'], Path(directory))
            trace = json.loads((Path(directory)/'trajectory.json').read_text())
        self.assertEqual(result, {'task_success': 100, 'action_count': 2})
        self.assertEqual(len(frames), 3)
        self.assertTrue(all(item['width'] == item['height'] == 256 for item in rendered))
        self.assertIn('policy_ms', trace['steps'][0])
        self.assertIn('gripper_position_before', trace['steps'][0])
        self.assertEqual(trace['recording']['frames'], 3)
        samples = [item['observation'] for item in channel.messages if item['type'] == 'step']
        self.assertNotIn('object-state', samples[0])
        self.assertNotIn('success', samples[0])


if __name__ == '__main__': unittest.main()
