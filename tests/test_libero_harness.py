"""Behavioral checks for skill feedback and the independently extracted starter."""
import importlib.util
import json
import math
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from evaluation_adapters.libero.robot_skills import Motion, rotation_error
from evaluation_adapters.libero_runner import checked_action
from build_wujie_starters import build


def observation(position=None, rotation=None):
    return {'eef_position': position or [0., 0., 1.],
            'eef_quaternion': rotation or [0., 0., 0., 1.], 'gripper_position': [.02, -.02]}


class HarnessTest(unittest.TestCase):
    def test_omitted_gripper_and_invalid_action_keep_closed_gripper(self):
        motion = Motion({'name': 'move', 'delta': [0, 0, .03]}, observation(), previous_gripper=1)
        self.assertEqual(motion.action(observation())[-1], 1)
        action, valid = checked_action({'type': 'action', 'value': [0, 0]}, previous_gripper=1)
        self.assertFalse(valid)
        self.assertEqual(action, [0, 0, 0, 0, 0, 0, 1])
        explicit = Motion({'name': 'gripper', 'gripper': -1}, observation(), previous_gripper=1)
        self.assertEqual(explicit.action(observation())[-1], -1)

    def test_move_uses_feedback_and_distinguishes_stall(self):
        motion = Motion({'name': 'move', 'delta': [0, 0, -.05], 'steps': 3}, observation())
        action = motion.action(observation())
        self.assertLess(action[2], 0)
        self.assertEqual(action[:2], [0., 0.])
        self.assertIsNone(motion.action(observation([0, 0, .95])))
        self.assertEqual(motion.feedback(observation([0, 0, .95]))['reason'], 'pose_reached')
        stalled = Motion({'name': 'move', 'delta': [.1, 0, 0], 'steps': 2}, observation())
        stalled.action(observation()); stalled.action(observation())
        self.assertIsNone(stalled.action(observation()))
        self.assertEqual(stalled.feedback(observation())['reason'], 'skill_step_limit')
        self.assertAlmostEqual(stalled.feedback(observation())['position_error_metres'], .1)

    def test_rotation_handles_quaternion_sign_and_axis(self):
        self.assertEqual(rotation_error([0, 0, 0, -1], [0, 0, 0, 1]), [0., 0., 0.])
        error = rotation_error([0, 0, math.sin(.1), math.cos(.1)], [0, 0, 0, 1])
        self.assertAlmostEqual(error[2], .2)

    def test_extracted_package_builds_importable_submission_without_secrets(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            build(root / 'downloads')
            extracted = root / 'starter'
            with zipfile.ZipFile(root / 'downloads/wujie-arm-starter.zip') as archive:
                archive.extractall(extracted)
            (extracted / '.env').write_text('UESTC_MODEL_API_KEY=test-private-value')
            subprocess.run([sys.executable, str(extracted / 'pack.py')], cwd=extracted,
                           check=True, capture_output=True)
            with zipfile.ZipFile(extracted / 'agent.zip') as archive:
                self.assertEqual(set(archive.namelist()), {'agent.py', 'model_api.py', 'robot_skills.py', 'config.json'})
                self.assertNotIn(b'test-private-value', b''.join(archive.read(n) for n in archive.namelist()))
            code = '''import sys, pathlib, json
sys.path.insert(0, sys.argv[1])
import agent
class Client:
 last_response = {'usage': {'total_tokens': 10}}
 def chat(self, *args, **kwargs):
  assert kwargs['json_mode'] is True
  return json.dumps({'skill': {'name': 'move', 'delta': [0, 0, -.02], 'steps': 5}, 'memory': {'target': 'bowl'}})
a = agent.Agent(); a.configure({'model': 'test'}); a.client = Client(); a.reset({'instruction': 'bowl'})
o = {'eef_position': [0,0,1], 'eef_quaternion': [0,0,0,1], 'gripper_position': [.02,-.02], 'step': 0, 'images_jpeg_base64': {'agentview': 'image1', 'robot0_eye_in_hand': 'image2'}}
assert a.act(o)[2] < 0
assert a.memory == {'target': 'bowl'}
'''
            subprocess.run([sys.executable, '-I', '-c', code, str(extracted)], cwd=root,
                           check=True, capture_output=True)


if __name__ == '__main__':
    unittest.main()
