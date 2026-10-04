"""Compute Minecraft observation metrics from trusted simulator telemetry.

Only the environment controller may provide this telemetry. Contestant code must
run in a separate sandbox without access to these inputs or /output/result.json.
"""

from __future__ import annotations

import math


def summarize_episode(telemetry: dict) -> dict[str, float]:
    required = telemetry["required_objectives"]
    completed = telemetry["completed_objectives"]
    if (not isinstance(required, list) or not required or
            any(not isinstance(item, str) for item in required) or len(required) != len(set(required))):
        raise ValueError("an episode needs distinct required objectives")
    if not isinstance(completed, list) or any(not isinstance(item, str) for item in completed) or not set(completed) <= set(required):
        raise ValueError("completed objectives must belong to the scenario")
    if len(completed) != len(set(completed)):
        raise ValueError("completed objectives must be unique")

    points = telemetry["positions"]
    if not isinstance(points, list) or not points:
        raise ValueError("an episode needs at least one position")
    if any(not isinstance(point, list) or len(point) != 3 or
           any(type(coord) not in (int, float) or not math.isfinite(coord) for coord in point)
           for point in points):
        raise ValueError("positions must contain finite 3D coordinates")
    distance = sum(math.dist(start, end) for start, end in zip(points, points[1:]))

    latencies = telemetry["step_latencies_ms"]
    if not isinstance(latencies, list) or any(type(value) not in (int, float) or
           not math.isfinite(value) or value < 0 for value in latencies):
        raise ValueError("step latencies must be nonnegative finite numbers")
    for key in ("deaths", "invalid_actions", "api_calls"):
        if type(telemetry[key]) is not int or telemetry[key] < 0:
            raise ValueError(f"{key} must be a nonnegative count")
    for key in ("collected_items", "tech_milestones"):
        if not isinstance(telemetry[key], list) or any(not isinstance(item, str) for item in telemetry[key]):
            raise ValueError(f"{key} must be a list of item identifiers")

    return {
        "task_success": 100.0 if len(completed) == len(required) else 0.0,
        "exploration_progress": 100.0 * len(completed) / len(required),
        "objectives": float(len(completed)),
        "unique_items": float(len(set(telemetry["collected_items"]))),
        "distance_blocks": distance,
        "tech_milestones": float(len(set(telemetry["tech_milestones"]))),
        "deaths": float(telemetry["deaths"]),
        "invalid_actions": float(telemetry["invalid_actions"]),
        "mean_step_ms": sum(latencies) / len(latencies) if latencies else 0.0,
        "api_calls": float(telemetry["api_calls"]),
    }
