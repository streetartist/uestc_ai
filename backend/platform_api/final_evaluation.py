"""Independent organizer-run LIBERO finals, using the existing trusted worker."""
from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

from flask import current_app


def required(problem):
    return (problem.evaluation_config or {}).get("adapter") == "libero-agent-v1" and (
        problem.scoring_config or {}).get("final_evaluation_required") is True


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                   allow_nan=False).encode()).hexdigest()


def scene_identity(scene):
    # Changing labels or RNG seed does not change a restored LIBERO init state.
    return digest({k: v for k, v in scene.items()
                   if k not in {"id", "label", "difficulty", "seed", "max_steps", "scoring"}})


LIBERO_FINAL_WEIGHTS = [10, 10, 10, 15, 15]


def validate_libero_final_scenes(scenes, public_scenes):
    """Three published tasks followed by two private two-target combinations."""
    from evaluation_adapters.wujie_scenes import LIBERO_TASKS
    if len(scenes) != 5:
        raise ValueError("LIBERO 正式计分包括三道公开题和两道组合题，共五题。")
    for index, (scene, count) in enumerate(zip(scenes, (1, 1, 2, 2, 2))):
        scoring = scene.get("scoring", {})
        if scoring.get("version") != 2 or len(scoring.get("reference_steps", [])) != count:
            raise ValueError("五题须分别配置1、1、2、2、2个目标的参考步数。")
        if index < 3:
            suite, task_id, name = LIBERO_TASKS[index][:3]
            if (scene.get("suite"), scene.get("task_id"), scene.get("task_name")) != (suite, task_id, name) or scene.get("scene_asset"):
                raise ValueError("前三题须按顺序保留黑碗放盘、汤罐入篮和双物品入篮。")
    public = {scene_identity(s) for s in public_scenes}
    hidden = [scene_identity(s) for s in scenes[3:]]
    if len(set(hidden)) != 2 or any(s in public for s in hidden):
        raise ValueError("后两道组合题须使用独立的新场景。")


def final_run(version):
    from .models import EvaluationRun
    run_id = (version.snapshot or {}).get("final_evaluation_run_id")
    run = EvaluationRun.query.filter_by(id=run_id, submission_version_id=version.id,
                                      purpose="final").first() if run_id else None
    if not run or run.submission_snapshot.get("captured_at") != version.snapshot.get("captured_at"):
        return None
    return run


def api_policy():
    """Freeze public API limits; credentials stay solely in server configuration."""
    raw = current_app.config.get("EVALUATION_FINAL_API_POLICY")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = None
    if not isinstance(raw, dict) or not {"allowed_models", "max_output_tokens", "reasoning_efforts"} <= set(raw):
        raise ValueError("请设置最终评测模型、输出 token 上限与 reasoning_efforts。")
    if set(raw) - {"allowed_models", "max_output_tokens", "reasoning_efforts", "max_request_bytes", "max_response_bytes", "timeout_seconds"}:
        raise ValueError("最终评测 API 配置包含未知字段。")
    models, efforts = raw["allowed_models"], raw["reasoning_efforts"]
    if (not isinstance(models, list) or not models or len(models) > 20
            or any(not isinstance(model, str) or not 1 <= len(model) <= 160 for model in models)
            or not isinstance(efforts, list) or not efforts
            or any(not isinstance(effort, str) or effort not in {"low", "medium", "high", "max"} for effort in efforts)):
        raise ValueError("最终评测模型与推理强度配置无效。")
    maximum = raw["max_output_tokens"]
    if type(maximum) is not int or not 1 <= maximum <= 393216:
        raise ValueError("最终评测输出 token 上限无效。")
    policy = dict(raw)
    for key, global_key in (("max_request_bytes", "AI_GATEWAY_MAX_REQUEST_BYTES"),
                            ("max_response_bytes", "AI_GATEWAY_MAX_RESPONSE_BYTES"),
                            ("timeout_seconds", "AI_GATEWAY_TIMEOUT_SECONDS")):
        cap = current_app.config[global_key]
        value = raw.get(key, cap)
        if type(value) is not int or not 1 <= value <= cap:
            raise ValueError(f"{key} 须在组织方 AI 网关上限内。")
        policy[key] = value
    return policy


def validate_api_body(body, policy):
    from .ai_gateway import GatewayError, validate_body
    if not isinstance(body, dict):
        raise GatewayError("请求必须是 JSON 对象。")
    if set(body) - {"model", "messages", "max_tokens", "max_completion_tokens", "thinking", "reasoning_effort",
                   "response_format", "temperature", "top_p", "stop", "stream", "tools", "tool_choice", "n"}:
        raise GatewayError("最终评测 API 请求包含未支持的参数。")
    if body.get("model") not in policy["allowed_models"]:
        raise GatewayError("该模型不在最终评测允许列表中。", 403, "model_not_allowed")
    if body.get("stream"):
        raise GatewayError("最终评测 API 使用完整响应。")
    if "thinking" in body and body["thinking"] not in ({"type": "enabled"}, {"type": "disabled"}):
        raise GatewayError("thinking 仅接受 enabled 或 disabled。")
    if "reasoning_effort" in body and body["reasoning_effort"] not in policy["reasoning_efforts"]:
        raise GatewayError("该推理强度不在最终评测允许列表中。")
    body = dict(body)
    # Explicit defaults avoid an implicit 1024-token truncation in shared validation.
    if not {"max_tokens", "max_completion_tokens"} & set(body):
        body["max_tokens"] = policy["max_output_tokens"]
    body, _, _ = validate_body(body, SimpleNamespace(max_output_tokens=policy["max_output_tokens"]), "chat/completions")
    if len(json.dumps(body, ensure_ascii=False).encode()) > policy["max_request_bytes"]:
        raise GatewayError("请求体超过最终评测上限。", 413, "request_too_large")
    return body
