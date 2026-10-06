import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from evaluation_docker_limits import check_memory_budget, docker_limits, failure_message, node_memory_budget
from evaluation_resources import required_memory_mb
from build_wujie_competition import build_catalogue


def config(adapter='minecraft-agent-v1', task='open-world', memory=2048):
    return {'adapter': adapter, 'task': task, 'resources': {'cpus': 4, 'memory_mb': memory}}


class DockerMemoryTests(unittest.TestCase):
    def test_agent_and_controller_have_separate_caps_and_no_swap(self):
        with patch.dict(os.environ, {'EVALUATION_CGROUP_PARENT': 'uestc-evaluation.slice'}):
            agent = docker_limits(config())
            mc = docker_limits(config(), controller=True)
            arm = docker_limits(config('libero-agent-v1', 'manipulation'), controller=True)
        for flags, memory in [(agent, 2048), (mc, 4096), (arm, 3072)]:
            self.assertIn(f'--memory={memory}m', flags)
            self.assertIn(f'--memory-swap={memory}m', flags)
            self.assertIn('--cgroup-parent=uestc-evaluation.slice', flags)
        self.assertEqual(required_memory_mb(config()), 6144)
        self.assertEqual(required_memory_mb(config('libero-agent-v1', 'manipulation')), 5120)

    def test_oversized_memory_is_rejected_before_execution(self):
        with patch('evaluation_docker_limits.node_memory_budget', return_value=6144):
            check_memory_budget(config())
            with self.assertRaises(RuntimeError):
                check_memory_budget(config(memory=8192))

    def test_host_reserve_cannot_be_disabled_by_oversized_configuration(self):
        with patch.dict(os.environ, {'EVALUATION_MEMORY_BUDGET_MB': '8192'}), \
             patch.object(Path, 'is_file', return_value=True), \
             patch.object(Path, 'read_text', return_value='MemTotal:       8136704 kB\n'):
            with self.assertRaises(ValueError): node_memory_budget()

    def test_default_budget_adapts_to_smaller_hosts(self):
        with patch.dict(os.environ, {}, clear=True), \
             patch.object(Path, 'is_file', return_value=True), \
             patch.object(Path, 'read_text', return_value='MemTotal:       6291456 kB\n'):
            self.assertEqual(node_memory_budget(), 4608)

    def test_oom_message_requires_confirmed_docker_oom(self):
        with patch('evaluation_docker_limits.subprocess.check_output', return_value='true\n'):
            self.assertIn('超过内存上限', failure_message('agent', '选手程序', 'failed'))
        with patch('evaluation_docker_limits.subprocess.check_output', return_value='false\n'):
            self.assertEqual(failure_message('agent', '选手程序', 'failed'), 'failed')

    def test_catalogue_publishes_the_actual_agent_memory_budget(self):
        problems = [t['problem'] for t in build_catalogue()['tracks']]
        self.assertEqual(problems[0]['evaluation_config']['resources']['memory_mb'], 6144)
        for problem in problems[1:]:
            self.assertEqual(problem['evaluation_config']['resources']['memory_mb'], 2048)
            self.assertIn('2GiB', problem['statement_md'])
            self.assertIn('超限会结束该次测试并计次', problem['statement_md'])


if __name__ == '__main__': unittest.main()
