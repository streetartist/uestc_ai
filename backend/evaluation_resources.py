"""Memory admission shared by the API and Docker executor (MiB)."""
CONTROLLER_MEMORY_MB = {
    'minecraft-agent-v1': 4096,
    'libero-agent-v1': 3072,
    'robot-arm-agent-v1': 3072,
}


def controller_memory_mb(config: dict) -> int:
    if config['adapter'] == 'minecraft-agent-v1' and config['task'] != 'open-world':
        return 0
    return CONTROLLER_MEMORY_MB.get(config['adapter'], 0)


def required_memory_mb(config: dict) -> int:
    return config['resources']['memory_mb'] + controller_memory_mb(config)


def memory_fits(capabilities: dict, config: dict) -> bool:
    # Old workers retain their protocol; upgraded Docker workers advertise the
    # aggregate capacity, including the trusted environment's separate cost.
    budget = capabilities.get('memory_mb')
    return budget is None or required_memory_mb(config) <= budget
