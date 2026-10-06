"""Bounded real OOM probe, followed by a healthy container on the same node."""
import argparse
import json
from pathlib import Path
import subprocess
from uuid import uuid4

from evaluation_docker_limits import docker_limits, failure_message
from evaluation_worker import remove_container
from evaluation_worker_lock import acquire_worker_lock


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--agent-image', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    config = {'adapter': 'classification-v1', 'task': 'classification',
              'resources': {'cpus': 1, 'memory_mb': 2048}}
    root = subprocess.check_output(['systemctl', 'show', 'uestc-evaluation.slice', '-p', 'ControlGroup', '--value'], text=True).strip()
    group = Path('/sys/fs/cgroup') / root.lstrip('/')
    assert (group/'memory.max').read_text().strip() == str(6*1024**3)
    assert (group/'memory.swap.max').read_text().strip() == '0'
    assert '--cgroup-parent=uestc-evaluation.slice' in docker_limits(config)
    name = 'uestc-bounded-oom-' + uuid4().hex
    command = ['docker', 'run', '--name', name, '--network=none', '--read-only',
               '--cap-drop=ALL', '--security-opt=no-new-privileges', '--pids-limit=32',
               '--user=65534:65534', *docker_limits(config), '--entrypoint=python', args.agent_image,
               '-c', 'chunks=[]\nfor _ in range(48): chunks.append(bytearray(64*1024*1024))']
    with acquire_worker_lock():
        try:
            result = subprocess.run(command, capture_output=True, timeout=90)
            state = json.loads(subprocess.check_output(['docker', 'inspect', name], text=True))[0]
            assert result.returncode == 137 and state['State']['OOMKilled'], state['State']
            assert state['HostConfig']['Memory'] == 2*1024**3
            assert state['HostConfig']['MemorySwap'] == 2*1024**3
            assert state['HostConfig']['CgroupParent'] == 'uestc-evaluation.slice'
            assert '超过内存上限' in failure_message(name, '选手程序', 'unconfirmed')
        finally:
            remove_container(name)
        config['resources']['memory_mb'] = 512
        healthy = subprocess.run(['docker', 'run', '--rm', '--network=none', *docker_limits(config),
            '--entrypoint=python', args.agent_image, '-c', 'print(len(bytearray(64*1024*1024)))'],
            capture_output=True, timeout=30, check=True)
        assert healthy.stdout.strip() == b'67108864'
    proof = {'status': 'passed', 'actual_oom_killed': True, 'agent_limit_mib': 2048,
             'container_swap_disabled': True, 'aggregate_limit_mib': 6144,
             'subsequent_container_healthy': True, 'oom_container_cleaned': True}
    args.output.write_text(json.dumps(proof, indent=2))
    print(json.dumps(proof), flush=True)


if __name__ == '__main__': main()
