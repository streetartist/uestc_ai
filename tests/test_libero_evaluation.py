import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from evaluation_adapters.libero_runner import checked_action, public_observation, run_episode, validate_scenarios
from evaluation_adapters.wujie_scenes import libero_scenes, minecraft_scenes
from platform_api.performance_scoring import performance_result


class Channel:
    def __init__(self, actions): self.actions, self.messages, self.ready = iter(actions), [], True
    def send(self, value): self.messages.append(value)
    def receive(self):
        if self.ready:
            self.ready = False
            return {'type': 'ready'}
        return {'type': 'action', 'value': next(self.actions)}


class FakeEnv:
    def __init__(self, success_at): self.steps, self.success_at = 0, success_at
    def step(self, action): self.steps += 1; return observation(), 999, True, {'success': True}
    def check_success(self): return self.steps >= self.success_at


def observation():
    return {'agentview_image': np.zeros((8,8,3), dtype=np.uint8), 'robot0_eye_in_hand_image': np.zeros((8,8,3),dtype=np.uint8),
            'robot0_eef_pos': [0,0,1], 'robot0_eef_quat': [0,0,0,1], 'robot0_gripper_qpos': [0,0],
            'robot0_joint_pos': [0]*7, 'object-state': [999], 'reward': 1}


class LiberoTests(unittest.TestCase):
    def test_baseline_reads_official_hdf5_observation_names(self):
        import runpy
        from types import SimpleNamespace
        from contextlib import nullcontext
        dataset = {'data': {'demo_0': {'actions': np.array([[.1,0,0,0,0,0,-1]]), 'obs': {
            'agentview_rgb': np.zeros((1,128,128,3), dtype=np.uint8),
            'eye_in_hand_rgb': np.zeros((1,128,128,3), dtype=np.uint8),
            'ee_pos': np.array([[.1,.2,.3]]), 'gripper_states': np.array([[.02,.02]])}}}}
        baseline = Path(__file__).resolve().parents[1]/'backend/evaluation_adapters/libero'
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/'policy.npz'
            with patch.dict(sys.modules, {'h5py': SimpleNamespace(File=lambda *_: nullcontext(dataset))}), \
                    patch.object(sys, 'argv', ['train_policy.py','--demo','public.hdf5','--output',str(output)]), \
                    patch.object(sys, 'path', [str(baseline), *sys.path]):
                runpy.run_path(str(baseline/'train_policy.py'), run_name='__main__')
            with np.load(output, allow_pickle=False) as weights:
                self.assertEqual(weights['features'].shape, (1,517))
                np.testing.assert_allclose(weights['actions'][0], [.1,0,0,0,0,0,-1])

    def test_classification_diagnostics_are_aggregate_only(self):
        from evaluation_evidence import classification_report
        matrix = [[0]*40 for _ in range(40)]
        matrix[0][0], matrix[0][1], matrix[1][1] = 3, 1, 2
        report = classification_report(matrix)
        self.assertEqual(set(report), {'confusion_matrix', 'per_class'})
        self.assertEqual(report['per_class'][0]['support'], 4)
        self.assertEqual(report['per_class'][0]['recall'], .75)
        self.assertAlmostEqual(report['per_class'][1]['precision'], 2/3)
        self.assertNotIn('predictions', report)

    def test_classification_publish_strips_private_sample_records(self):
        import json
        from evaluation_evidence import publish_classification
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/'run'/'0'; output.mkdir(parents=True)
            matrix = [[0]*40 for _ in range(40)]; matrix[3][3]=12
            (output/'evidence.json').write_text(json.dumps({'confusion_matrix':matrix,
                'predictions':[{'file':'hidden-user-ID.npy','label':3}], 'private_path':'secret'}))
            with patch('evaluation_evidence.upload') as upload:
                publish_classification('https://example.com/api', {'id':'run','config':{'resources':{'episodes':1}}},directory)
            report = upload.call_args.args[3].read_text()
            self.assertNotIn('hidden-user-ID',report)
            self.assertNotIn('secret',report)
            self.assertEqual(json.loads(report)['per_class'][3]['support'],12)

    def test_native_actions_reject_nan_bool_and_self_reported_success(self):
        for value in ([0]*6, [float('nan')]*7, [True]*7, {'task_success': 100}, [2]*7):
            self.assertFalse(checked_action({'type': 'action', 'value': value})[1])
        self.assertTrue(checked_action({'type': 'action', 'value': [0,0,0,0,0,0,-1]})[1])

    def test_only_native_predicate_controls_success_and_observation_omits_truth(self):
        scene = libero_scenes()[0]; scene['max_steps'] = 3
        channel = Channel([{'task_success': 100}, [0]*7, [0]*7])
        with tempfile.TemporaryDirectory() as directory:
            metrics = run_episode(channel, FakeEnv(999), observation(), 'task', scene,
                ['task_success', 'action_count', 'invalid_actions'], Path(directory))
            self.assertEqual(metrics, {'task_success': 0, 'action_count': 3, 'invalid_actions': 1})
            self.assertTrue((Path(directory)/'replay.gif').is_file())
        sample = public_observation(observation(), 0, True)
        self.assertFalse({'object-state', 'reward', 'success'} & set(sample))
        self.assertNotIn('init_state_id', channel.messages[0]['goal'])
        self.assertNotIn('seed', channel.messages[0]['goal'])

    def test_original_tasks_and_different_initial_states_share_success_scoring(self):
        validate_scenarios(libero_scenes(), 3)
        hidden = libero_scenes((20,21,22), (100,101,102))
        validate_scenarios(hidden, 3)
        self.assertEqual([s['task_name'] for s in hidden], [s['task_name'] for s in libero_scenes()])
        hidden[0]['init_state_id'] = 50
        with self.assertRaises(ValueError): validate_scenarios(hidden, 3)
        score = performance_result({'preset': 'wujie-libero-v1', 'criterion': '表现'}, 'libero-agent-v1',
            [{'task_success': value} for value in [100,0,100]])
        self.assertAlmostEqual(score['score'], 66.666667)


if __name__ == '__main__': unittest.main()
