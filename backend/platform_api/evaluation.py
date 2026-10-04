from __future__ import annotations

import math
import json
import os
import re
from copy import deepcopy
from pathlib import Path


# An adapter describes a trusted worker image's protocol, not an executable supplied by a contestant.
ADAPTERS = {
    "minecraft-agent-v1": {
        "name": "Minecraft 开放世界智能体",
        "submission": {"extension": "zip", "label": "智能体代码与运行配置（.zip）"},
        "tasks": ["survival", "collection", "navigation", "open-world"],
        "metrics": [
            {"key": "task_success", "label": "任务完成率", "unit": "%", "direction": "max", "min": 0, "max": 100},
            {"key": "survival_seconds", "label": "Survival time", "unit": "s", "direction": "max", "min": 0, "max": 3600},
            {"key": "objectives", "label": "已完成目标数", "unit": "count", "direction": "max", "min": 0, "max": 20},
            {"key": "deaths", "label": "死亡次数", "unit": "count", "direction": "min", "min": 0, "max": 10},
            {"key": "invalid_actions", "label": "无效动作数", "unit": "count", "direction": "min", "min": 0, "max": 100},
            {"key": "mean_step_ms", "label": "平均决策耗时", "unit": "ms", "direction": "min", "min": 0, "max": 120000},
            {"key": "api_calls", "label": "API calls", "unit": "count", "direction": "min", "min": 0, "max": 1000},
            {"key": "api_cost", "label": "API cost", "unit": "USD", "direction": "min", "min": 0, "max": 10},
            {"key": "unique_items", "label": "发现物品种类", "unit": "count", "direction": "max", "min": 0, "max": 512},
            {"key": "distance_blocks", "label": "累计移动距离", "unit": "blocks", "direction": "max", "min": 0, "max": 100000},
            {"key": "tech_milestones", "label": "科技树里程碑", "unit": "count", "direction": "max", "min": 0, "max": 40},
            {"key": "exploration_progress", "label": "探索进度（目标完成比例）", "unit": "%", "direction": "max", "min": 0, "max": 100},
        ],
    },
    "classification-v1": {
        "name": "Classification inference",
        "submission": {"extension": "zip", "label": "Inference package and weights (.zip)"},
        "tasks": ["classification"],
        "metrics": [
            {"key": "accuracy", "label": "Accuracy", "unit": "%", "direction": "max", "min": 0, "max": 100},
            {"key": "macro_f1", "label": "Macro F1", "unit": "%", "direction": "max", "min": 0, "max": 100},
            {"key": "latency_ms", "label": "Inference latency", "unit": "ms", "direction": "min", "min": 0, "max": 5000},
            {"key": "peak_vram_mb", "label": "Peak GPU memory", "unit": "MB", "direction": "min", "min": 0, "max": 24576},
        ],
    },
    "robot-arm-agent-v1": {
        "name": "Robot arm agent",
        "submission": {"extension": "zip", "label": "Agent code and configuration (.zip)"},
        "tasks": ["pick-place", "multi-step"],
        "metrics": [
            {"key": "task_success", "label": "Task success", "unit": "%", "direction": "max", "min": 0, "max": 100},
            {"key": "stable_grasps", "label": "Stable grasps", "unit": "%", "direction": "max", "min": 0, "max": 100},
            {"key": "recovery_success", "label": "Recovery success", "unit": "%", "direction": "max", "min": 0, "max": 100},
            {"key": "action_count", "label": "Actions used", "unit": "count", "direction": "min", "min": 0, "max": 10000},
            {"key": "execution_seconds", "label": "Execution time", "unit": "s", "direction": "min", "min": 0, "max": 14400},
        ],
    },
}


def adapters() -> dict[str, dict]:
    registered = deepcopy(ADAPTERS)
    directory = os.environ.get("EVALUATION_ADAPTER_MANIFEST_DIR", "")
    if not directory:
        return registered
    for path in sorted(Path(directory).glob("*.json")):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or set(manifest) != {"id", "name", "submission", "tasks", "metrics"}:
            raise ValueError(f"invalid evaluation manifest: {path.name}")
        identifier = manifest.pop("id")
        if not isinstance(identifier, str) or not re.fullmatch(r"[a-z][a-z0-9-]{2,63}", identifier) or identifier in registered:
            raise ValueError(f"duplicate or invalid evaluation adapter: {path.name}")
        submission, tasks, metrics = manifest["submission"], manifest["tasks"], manifest["metrics"]
        if not isinstance(manifest["name"], str) or not 1 <= len(manifest["name"]) <= 80:
            raise ValueError(f"invalid adapter name: {path.name}")
        if not isinstance(submission, dict) or set(submission) != {"extension", "label"} or submission["extension"] != "zip" or not isinstance(submission["label"], str):
            raise ValueError(f"invalid submission protocol: {path.name}")
        if not isinstance(tasks, list) or not tasks or len(tasks) > 30 or any(not isinstance(task, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", task) for task in tasks) or len(tasks) != len(set(tasks)):
            raise ValueError(f"invalid adapter tasks: {path.name}")
        if not isinstance(metrics, list) or not 1 <= len(metrics) <= 64:
            raise ValueError(f"invalid adapter metrics: {path.name}")
        keys = set()
        for metric in metrics:
            if not isinstance(metric, dict) or set(metric) != {"key", "label", "unit", "direction", "min", "max"}:
                raise ValueError(f"invalid metric declaration: {path.name}")
            key = metric["key"]
            if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key) or key in keys:
                raise ValueError(f"invalid or duplicate metric key: {path.name}")
            keys.add(key)
            if not isinstance(metric["label"], str) or not 1 <= len(metric["label"]) <= 80 or not isinstance(metric["unit"], str) or len(metric["unit"]) > 20 or metric["direction"] not in {"min", "max"}:
                raise ValueError(f"invalid metric metadata: {path.name}")
            low, high = metric["min"], metric["max"]
            if type(low) not in (int, float) or type(high) not in (int, float) or not math.isfinite(low) or not math.isfinite(high) or low >= high:
                raise ValueError(f"invalid metric range: {path.name}")
        registered[identifier] = manifest
    return registered


def catalog() -> list[dict]:
    from flask import current_app

    enabled = {item.strip() for item in current_app.config["EVALUATION_ENABLED_ADAPTERS"].split(",") if item.strip()}
    return [{"id": key, **value, "available": key in enabled and bool(current_app.config["EVALUATION_WORKER_TOKEN"])} for key, value in adapters().items()]


def validate_evaluation_config(value: object) -> dict:
    if value in (None, {}):
        return {}
    if not isinstance(value, dict) or set(value) != {"adapter", "task", "resources", "api", "metrics"}:
        raise ValueError("evaluation config has invalid fields")
    if not isinstance(value["adapter"], str) or not isinstance(value["task"], str):
        raise ValueError("unknown evaluation adapter or task")
    adapter = adapters().get(value["adapter"])
    if not adapter or value["task"] not in adapter["tasks"]:
        raise ValueError("unknown evaluation adapter or task")
    resources = value["resources"]
    if not isinstance(resources, dict) or set(resources) != {"cpus", "memory_mb", "gpu", "time_seconds", "episodes"}:
        raise ValueError("invalid evaluation resources")
    for field, low, high in (("cpus", 1, 16), ("memory_mb", 512, 65536), ("time_seconds", 30, 14400), ("episodes", 1, 30)):
        number = resources[field]
        if type(number) is not int or not low <= number <= high:
            raise ValueError(f"{field} must be between {low} and {high}")
    if type(resources["gpu"]) is not bool:
        raise ValueError("gpu must be a boolean")
    api = value["api"]
    if not isinstance(api, dict) or set(api) != {"enabled", "max_calls"} or type(api["enabled"]) is not bool:
        raise ValueError("invalid evaluation API policy")
    if type(api["max_calls"]) is not int or not 0 <= api["max_calls"] <= 100000:
        raise ValueError("max_calls must be between 0 and 100000")
    if not api["enabled"] and api["max_calls"] != 0:
        raise ValueError("disabled API must have zero calls")
    metrics = value["metrics"]
    allowed = {metric["key"] for metric in adapter["metrics"]}
    if not isinstance(metrics, list) or not metrics or any(not isinstance(key, str) for key in metrics) or len(metrics) != len(set(metrics)) or set(metrics) - allowed:
        raise ValueError("unknown or empty evaluation metrics")
    if value["adapter"] == "minecraft-agent-v1" and value["task"] == "open-world" and "api_calls" in metrics:
        raise ValueError("Minecraft API calls are available per run, not per episode")
    return deepcopy(value)


def validate_metrics(config: dict, values: object) -> dict[str, float]:
    declared = config.get("metric_definitions")
    if declared is None:
        adapter = adapters().get(config["adapter"])
        if not adapter:
            raise ValueError("evaluation adapter is no longer registered")
        declared = adapter["metrics"]
    definitions = {metric["key"]: metric for metric in declared}
    if not isinstance(values, dict) or set(values) != set(config["metrics"]):
        raise ValueError("evaluation metrics are missing or unknown")
    if any(type(value) not in (float, int) or not math.isfinite(value) for value in values.values()):
        raise ValueError("evaluation metrics must be finite numbers")
    normalized = {key: float(value) for key, value in values.items()}
    for key, value in normalized.items():
        metric = definitions[key]
        if not metric["min"] <= value <= metric["max"]:
            raise ValueError(f"{key} is outside its declared range")
    return normalized
