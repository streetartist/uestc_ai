"""Versioned, deterministic scores from organizer-measured episode metrics."""
from __future__ import annotations

import math

PRESETS = {
    "wujie-depth-v1": ("classification-v1", {"accuracy", "macro_f1", "latency_ms"}),
    "wujie-depth-baseline-v2": ("classification-v1", {"accuracy", "macro_f1", "latency_ms"}),
    "wujie-world-v1": ("minecraft-agent-v1", {"task_success", "exploration_progress"}),
    "wujie-arm-v1": ("robot-arm-agent-v1", {"task_success", "stable_grasps", "action_count"}),
    "wujie-libero-v1": ("libero-agent-v1", {"task_success"}),
}


def validate_performance_scoring(scoring, evaluation=None):
    if scoring is None:
        return None
    reference_keys = {"baseline_accuracy", "gain_span_pp", "latency_ms"}
    is_baseline = isinstance(scoring, dict) and scoring.get("preset") == "wujie-depth-baseline-v2"
    expected_keys = {"preset", "criterion", "reference"} if is_baseline else {"preset", "criterion"}
    if (not isinstance(scoring, dict) or set(scoring) != expected_keys
            or not isinstance(scoring.get("preset"), str)
            or scoring.get("preset") not in PRESETS
            or not isinstance(scoring.get("criterion"), str) or not 1 <= len(scoring["criterion"]) <= 80):
        raise ValueError("自动表现分须指定支持的版本与评分项。")
    if is_baseline:
        reference = scoring["reference"]
        if (not isinstance(reference, dict) or set(reference) != reference_keys
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in reference.values())):
            raise ValueError("基准计分须指定有限的准确率、提升幅度与延迟参考值。")
        baseline = reference["baseline_accuracy"]
        if (not 0 < baseline < 100 or not 0 < reference["gain_span_pp"] <= 100 - baseline
                or reference["latency_ms"] <= 0):
            raise ValueError("基准准确率须在0与100之间，提升幅度须可达，延迟参考值须大于0。")
    if evaluation is not None:
        adapter, metrics = PRESETS[scoring["preset"]]
        if evaluation.get("adapter") != adapter or not metrics <= set(evaluation.get("metrics", [])):
            raise ValueError("自动表现分与测评器或所选指标不匹配。")
    return {**scoring, **({"reference": dict(scoring["reference"])} if is_baseline else {})}


def performance_result(scoring, adapter, episodes):
    if not scoring:
        return None
    validate_performance_scoring(scoring)
    expected, required = PRESETS[scoring["preset"]]
    if adapter != expected or not isinstance(episodes, list) or not episodes:
        raise ValueError("自动表现分需要完整的可信测评结果。")
    if scoring["preset"] == "wujie-depth-baseline-v2" and len(episodes) != 1:
        raise ValueError("分类基准计分须使用完整评测集的一组汇总指标。")
    scores = []
    for episode in episodes:
        metrics = episode.get("metrics", episode)
        if not required <= set(metrics):
            raise ValueError("可信测评结果缺少计分指标。")
        for key in required:
            value = metrics[key]
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError("计分指标须为有限非负数。")
            if key not in {"latency_ms", "action_count"} and value > 100:
                raise ValueError("百分制指标不能超过100。")
        if scoring["preset"] == "wujie-depth-baseline-v2":
            reference = scoring["reference"]
            accuracy = metrics["accuracy"]
            latency = metrics["latency_ms"]
            if latency <= 0:
                raise ValueError("完整分类测评的平均延迟须大于0。")
            attainment = min(accuracy / reference["baseline_accuracy"], 1)
            components = {
                "baseline": 60 * attainment,
                "improvement": 20 * min(max(accuracy - reference["baseline_accuracy"], 0) / reference["gain_span_pp"], 1),
                "macro_f1": .1 * metrics["macro_f1"],
                "efficiency": 10 * attainment * min(1, reference["latency_ms"] / latency),
            }
            return {**scoring, "score": sum(components.values()), "components": components}
        elif scoring["preset"] == "wujie-depth-v1":
            accuracy = metrics["accuracy"]
            speed = min(1, 100 / max(metrics["latency_ms"], 0.001))
            # Efficiency points scale with accuracy; a fast empty model earns no points.
            score = .8 * accuracy + .1 * metrics["macro_f1"] + .1 * accuracy * speed
        elif scoring["preset"] == "wujie-world-v1":
            score = .7 * metrics["exploration_progress"] + .3 * metrics["task_success"]
        elif scoring["preset"] == "wujie-libero-v1":
            score = metrics["task_success"]
        else:
            efficiency = max(0, 1 - metrics["action_count"] / 600)
            score = .8 * metrics["task_success"] + .1 * metrics["stable_grasps"] + .1 * metrics["task_success"] * efficiency
        scores.append(score)
    return {**scoring, "score": round(sum(scores) / len(scores), 6)}


def validate_problem_scoring(config, evaluation, judging):
    scoring = config.get("performance_scoring")
    if not scoring:
        return
    validate_performance_scoring(scoring, evaluation)
    rubric = judging.get("rubric", {})
    if (config.get("review_score_mode") != "weighted" or scoring["criterion"] not in rubric
            or any(type(w) not in (int, float) or not math.isfinite(w) or w < 0 for w in rubric.values())
            or not math.isclose(sum(rubric.values()), 1)):
        raise ValueError("自动表现分的评分项须存在，且加权评分合计100%。")
