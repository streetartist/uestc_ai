"""Host reserve, Docker hard limits and truthful OOM errors."""
import os
from pathlib import Path
import re
import subprocess

from evaluation_resources import controller_memory_mb, required_memory_mb


def node_memory_budget() -> int:
    configured = os.environ.get('EVALUATION_MEMORY_BUDGET_MB')
    value = configured if configured is not None else '6144'
    if not value.isdecimal() or not 512 <= int(value) <= 1048576:
        raise ValueError('EVALUATION_MEMORY_BUDGET_MB must be a positive memory budget in MiB')
    budget = int(value)
    meminfo = Path('/proc/meminfo')
    if meminfo.is_file():
        total = next(int(line.split()[1]) // 1024 for line in meminfo.read_text().splitlines() if line.startswith('MemTotal:'))
        available = total - 1536
        if configured is None:
            budget = min(budget, available)
        if budget < 512 or budget > available:
            raise ValueError('Docker memory budget must leave at least 1536 MiB for the host')
    return budget


def check_memory_budget(config: dict):
    if required_memory_mb(config) > node_memory_budget():
        raise RuntimeError('测评配置的程序与环境内存合计超过节点容量，尚未执行程序。')


def docker_limits(config: dict, *, controller=False) -> list[str]:
    memory = controller_memory_mb(config) if controller else config['resources']['memory_mb']
    flags = [f"--cpus={config['resources']['cpus']}", f'--memory={memory}m', f'--memory-swap={memory}m']
    parent = os.environ.get('EVALUATION_CGROUP_PARENT', '')
    if parent:
        if not re.fullmatch(r'[a-zA-Z0-9_.-]+\.slice', parent):
            raise ValueError('EVALUATION_CGROUP_PARENT must name a systemd slice')
        flags.append('--cgroup-parent=' + parent)
    return flags


def failure_message(container: str, label: str, fallback: str) -> str:
    try:
        result = subprocess.check_output(['docker', 'inspect', '--format', '{{.State.OOMKilled}}', container],
                                         text=True, stderr=subprocess.DEVNULL, timeout=5).strip()
        if result == 'true':
            return f'{label}超过内存上限，本次测试已终止。'
    except (OSError, subprocess.SubprocessError):
        pass
    return fallback
