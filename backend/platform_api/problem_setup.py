"""Problem setup transactions and portable packages. No cloud calls or execution."""
from __future__ import annotations

import io
import json
import math
import re
import stat
import zipfile
import zlib
from copy import deepcopy
from datetime import timedelta

from flask import current_app

from .ai_gateway import GatewayError, decrypt_keys
from .ai_models import AIChannel, AIProblemQuota
from .ai_quotas import lock_competition, lock_problem
from .compute import worker_online
from .compute_models import ComputePolicy, ComputeProvider
from .evaluation import catalog, fixed_evaluation_rules, validate_evaluation_config
from .extensions import db
from .models import EvaluationWorkerState, Problem, ProblemRuntime, Submission, utcnow
from .reviewing import validate_scoring_config
from .security import current_user
from .submission_schema import validate_submission_schema
from .utils import audit


def validate_runtime(value, evaluation):
    if value is None:
        return None
    if not evaluation:
        raise ValueError("请先选择评测器，再设置 Docker 环境。")
    if not isinstance(value, dict) or set(value) - {"image", "agent_image", "scenarios"}:
        raise ValueError("Docker 环境只接受镜像、智能体镜像和测试场景。")
    value = deepcopy(value)
    for name in ("image", "agent_image"):
        image = value.get(name, "")
        if not isinstance(image, str) or (name == "image" or image) and not re.fullmatch(r"(?:[^\s]{1,250}@)?sha256:[a-f0-9]{64}", image):
            raise ValueError("Docker 镜像须填写固定 sha256 摘要，不能使用 latest 等可变标签。")
    scenes = value.get("scenarios", [])
    if not isinstance(scenes, list) or len(json.dumps(scenes, allow_nan=False).encode()) > 1024 * 1024:
        raise ValueError("私有测试场景须为 JSON 列表，且不超过 1 MB。")
    if evaluation["adapter"] == "minecraft-agent-v1" and evaluation["task"] == "open-world":
        if not value.get("agent_image"):
            raise ValueError("MC 开放世界需要独立的智能体镜像。")
        from evaluation_adapters.minecraft_runner import validate_scenarios
        validate_scenarios(scenes, evaluation["resources"]["episodes"])
    elif evaluation["adapter"] == "robot-arm-agent-v1":
        if not value.get("agent_image"):
            raise ValueError("机械臂需要独立的智能体镜像。")
        from evaluation_adapters.robot_arm_runner import validate_scenarios
        validate_scenarios(scenes, evaluation["resources"]["episodes"])
    elif value.get("agent_image"):
        raise ValueError("独立智能体镜像只用于 MC 或机械臂评测器。")
    elif scenes and len(scenes) != evaluation["resources"]["episodes"]:
        raise ValueError("私有场景数量必须与每次测试的场景数量一致。")
    return {"image": value["image"], "agent_image": value.get("agent_image", ""), "scenarios": scenes}


def normalize_setup(data, existing_evaluation=None):
    from .routes.ai import quota_values
    from .routes.compute import compute_quota_values
    if not isinstance(data, dict) or set(data) - {"evaluation_config", "runtime", "ai", "compute"}:
        raise ValueError("环境配置格式不正确。")
    result = {}
    evaluation = validate_evaluation_config(data.get("evaluation_config", existing_evaluation or {}))
    if "evaluation_config" in data:
        result["evaluation_config"] = evaluation
    if "runtime" in data:
        result["runtime"] = validate_runtime(data["runtime"], evaluation)
    for key, parser in (("ai", quota_values), ("compute", compute_quota_values)):
        if key in data:
            if not isinstance(data[key], dict):
                raise ValueError("关闭已有资源请取消启用，不能删除其用量记录。")
            result[key] = parser(data[key])
    return result


def runtime_images_allowed(runtime, evaluation):
    if runtime is None or current_user().role == "admin":
        return
    # Organizers can reuse an admin-approved image pair, including new private cases.
    for saved in ProblemRuntime.query.all():
        if saved.problem.evaluation_config.get("adapter") == evaluation.get("adapter") and all(
            saved.config.get(key, "") == runtime.get(key, "") for key in ("image", "agent_image")
        ):
            return
    raise GatewayError("请先由管理员登记可信镜像，再选择复用环境。", 403)


def save_setup(problem, data):
    from .routes.ai import apply_problem_quota
    from .routes.compute import apply_problem_compute
    lock_competition(problem.track.competition_id)
    lock_problem(problem.id)
    db.session.refresh(problem)
    data = normalize_setup(data, problem.evaluation_config)
    evaluation = data.get("evaluation_config", problem.evaluation_config)
    previous = db.session.get(ProblemRuntime, problem.id, populate_existing=True)
    runtime = data.get("runtime", previous.config if previous else None)
    if runtime:
        validate_runtime(runtime, evaluation)
    if Submission.query.filter_by(problem_id=problem.id).first() and (
        fixed_evaluation_rules(evaluation) != fixed_evaluation_rules(problem.evaluation_config)
        or ("runtime" in data and runtime != (previous.config if previous else None))
    ):
        raise GatewayError("已有作品提交，不能更换测评规则、镜像或私有场景；可调整时长、次数与资源额度。", 409)
    if "runtime" in data:
        runtime_images_allowed(runtime, evaluation)
        if runtime is None and previous:
            db.session.delete(previous)
        elif runtime is not None:
            previous = previous or ProblemRuntime(problem_id=problem.id)
            previous.config = runtime
            db.session.add(previous)
    problem.evaluation_config = evaluation
    if "ai" in data:
        apply_problem_quota(problem, data["ai"])
    if "compute" in data:
        apply_problem_compute(problem, data["compute"])
    audit("problem.setup.saved", "problem", problem.id, {"sections": sorted(data)})
    # Caller commits once, after every section has passed validation.
    db.session.flush()


def setup_data(problem):
    runtime = db.session.get(ProblemRuntime, problem.id)
    ai = db.session.get(AIProblemQuota, problem.id)
    compute = db.session.get(ComputePolicy, problem.id)
    return {"evaluation_config": problem.evaluation_config, "runtime": runtime.config if runtime else None,
            "ai": ai.config if ai else None, "compute": compute.config if compute else None}


def readiness(problem):
    data = setup_data(problem)
    evaluation, runtime, ai, compute = (data[key] for key in ("evaluation_config", "runtime", "ai", "compute"))
    checks = []
    def add(key, label, state, detail):
        checks.append({"key": key, "label": label, "state": state, "detail": detail})
    if evaluation:
        adapter = next((item for item in catalog() if item["id"] == evaluation["adapter"]), None)
        add("adapter", "评测器接入", "ready" if adapter and adapter["available"] else "blocked", "已启用评测器" if adapter and adapter["available"] else "管理员需要启用此评测器并配置测评端认证。")
        add("runtime", "Docker 环境", "ready" if runtime else "warning", "固定镜像与私有场景已保存" if runtime else "尚未设置题目环境，将使用运行端原有配置。")
        workers = EvaluationWorkerState.query.filter(EvaluationWorkerState.seen_at > utcnow() - timedelta(seconds=75)).all()
        compatible = any(evaluation["adapter"] in w.capabilities["adapters"]
            and (not runtime or w.capabilities.get("managed_runtime"))
            and (runtime or evaluation["adapter"] in w.capabilities.get("legacy_adapters", w.capabilities["adapters"]))
            and (not evaluation["resources"]["gpu"] or w.capabilities["gpu"])
            and (not evaluation["api"]["enabled"] or w.capabilities.get("api_proxy", True)) for w in workers)
        add("worker", "测评端在线", "ready" if compatible else "blocked", "已发现可处理本题的在线测评端" if compatible else "没有匹配的在线测评端，请启动 Docker 测评服务。")
        add("limits", "测试限制", "ready", f"每次 {evaluation['resources']['time_seconds']} 秒；每队 {evaluation.get('max_team_runs', '不限')} 次")
    else:
        add("evaluation", "指标测试", "unused", "本题只收作品材料，不自动运行程序。")
    if ai:
        channels = AIChannel.query.filter_by(enabled=True).all()
        def has_keys(channel):
            try:
                return bool(decrypt_keys(channel.secrets))
            except GatewayError:
                return False
        models = {model for channel in channels if not ai["allowed_channels"] or channel.id in ai["allowed_channels"]
                  for model in channel.models if model not in channel.disabled_models and has_keys(channel)}
        missing = sorted(set(ai["allowed_models"]) - models)
        required = bool(evaluation and evaluation["api"]["enabled"])
        empty = ai["max_tokens"] == 0 or ai["max_calls"] == 0 or ai.get("max_cost_micros") == 0 or required and evaluation["api"]["max_calls"] == 0
        add("api", "模型 API", "blocked" if required and (not ai["enabled"] or empty) else "unused" if not ai["enabled"] else "blocked" if missing else "warning" if empty else "ready",
            "指标测试需要 API，但本题 API 已暂停。" if required and not ai["enabled"] else "本题 API 已暂停" if not ai["enabled"] else "可调用额度为零，请调整额度。" if empty else "缺少可用模型：" + "、".join(missing) if missing else "模型渠道与每队额度已配置")
    elif evaluation and evaluation["api"]["enabled"]:
        fallback = bool(current_app.config.get("EVALUATION_API_URL") and current_app.config.get("EVALUATION_API_KEY"))
        add("api", "模型 API", "warning" if fallback else "blocked", "使用运行端默认 API，建议设置本题统一额度。" if fallback else "请配置本题模型和 API 额度。")
    else:
        add("api", "模型 API", "unused", "未分配本题模型 API。")
    if compute:
        provider = db.session.get(ComputeProvider, compute["provider_id"])
        ok = provider and provider.enabled and provider.secret and worker_online()
        add("compute", "AutoDL 与 SSH", "unused" if not compute["enabled"] else "ready" if ok else "blocked",
            "队伍自用算力已暂停" if not compute["enabled"] else "渠道已配置，开机后本队可获取 SSH 和实例工具。" if ok else "请检查算力渠道启用、Token 与算力服务在线状态。")
    else:
        add("compute", "AutoDL 与 SSH", "unused", "未分配队伍自用算力。")
    return {"checks": checks, "ready": not any(c["state"] == "blocked" for c in checks)}


PACKAGE_FILES = {"problem.json", "statement.md", "runtime.json", "scenarios.json", "connections.json", "README.md"}


def read_package(raw):
    if len(raw) > 20 * 1024 * 1024:
        raise ValueError("赛题包不能超过 20 MB。")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            infos = archive.infolist()
            names = [item.filename for item in infos]
            if (not infos or len(infos) > 10 or len(names) != len(set(names))
                or any(name not in PACKAGE_FILES for name in names)
                or any(stat.S_ISLNK(item.external_attr >> 16) or item.flag_bits & 1 for item in infos)
                or sum(item.file_size for item in infos) > 4 * 1024 * 1024
                or any(item.file_size > 2 * 1024 * 1024 for item in infos)):
                raise ValueError("赛题包须使用规定文件，不能包含目录、重复文件、链接或超大内容。")
            files = {name: archive.read(name).decode("utf-8-sig") for name in names}
        manifest = json.loads(files["problem.json"])
        if not isinstance(manifest, dict) or manifest.get("version") != 1 or set(manifest) - {"version", "problem", "setup"}:
            raise ValueError("不支持此赛题包格式，请使用 version: 1。")
        problem = manifest.get("problem")
        if not isinstance(problem, dict):
            raise ValueError("赛题包缺少 problem 配置。")
        from .routes.manage import PROBLEM_FIELDS
        if set(problem) - PROBLEM_FIELDS:
            raise ValueError("赛题包含有不支持的题目字段。")
        problem = deepcopy(problem)
        for key, maximum in (("code", 40), ("slug", 120), ("title", 240)):
            if not isinstance(problem.get(key), str) or not 1 <= len(problem[key].strip()) <= maximum:
                raise ValueError(f"请填写有效的 {key}。")
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", problem["slug"]):
            raise ValueError("题目标识只可使用小写字母、数字和连字符。")
        for key in ("summary", "statement_md", "compute_note"):
            if key in problem and not isinstance(problem[key], str):
                raise ValueError(f"{key} 须为文本。")
        source_url = problem.get("source_url")
        if source_url == "":
            problem["source_url"] = source_url = None
        if source_url is not None and (not isinstance(source_url, str) or len(source_url) > 500 or not re.match(r"^https?://", source_url)):
            raise ValueError("参考资料 URL 须为有效的 HTTP 或 HTTPS 地址。")
        if type(problem.get("difficulty", 3)) is not int or not 1 <= problem.get("difficulty", 3) <= 5:
            raise ValueError("难度须为 1—5 的整数。")
        rubric = problem.get("judging_schema", {})
        if not isinstance(rubric, dict) or not isinstance(rubric.get("rubric", {}), dict):
            raise ValueError("评分项格式不正确。")
        if any(not isinstance(key, str) or not 1 <= len(key) <= 80 or type(weight) not in (int, float)
               or not math.isfinite(weight) or weight < 0 for key, weight in rubric.get("rubric", {}).items()):
            raise ValueError("评分项须有名称和非负数值权重。")
        problem["status"] = "draft"
        problem["submission_schema"] = validate_submission_schema(problem.get("submission_schema"))
        problem["scoring_config"] = validate_scoring_config(problem.get("scoring_config"))
        if "statement.md" in files:
            problem["statement_md"] = files["statement.md"]
        setup = manifest.get("setup", {})
        if not isinstance(setup, dict):
            raise ValueError("setup 须为对象。")
        setup = deepcopy(setup)
        if "runtime.json" in files:
            if "runtime" in setup:
                raise ValueError("不要同时在 setup 与 runtime.json 中重复配置环境。")
            setup["runtime"] = json.loads(files["runtime.json"])
        if "scenarios.json" in files:
            if not isinstance(setup.get("runtime"), dict):
                raise ValueError("scenarios.json 需要配套 runtime.json。")
            setup["runtime"]["scenarios"] = json.loads(files["scenarios.json"])
        setup["evaluation_config"] = setup.get("evaluation_config", problem.get("evaluation_config", {}))
        problem["evaluation_config"] = validate_evaluation_config(setup["evaluation_config"])
        connections = json.loads(files.get("connections.json", "{}"))
        if not isinstance(connections, dict) or set(connections) - {"channels", "provider"}:
            raise ValueError("渠道绑定格式不正确。")
        return problem, setup, connections
    except (zipfile.BadZipFile, zipfile.LargeZipFile, KeyError, UnicodeError, json.JSONDecodeError,
            TypeError, OverflowError, RuntimeError, NotImplementedError, zlib.error) as error:
        raise ValueError("赛题包内容不完整或格式错误。") from error


def bind_connections(setup, connections, overrides):
    setup = deepcopy(setup)
    if not isinstance(overrides, dict) or set(overrides) - {"allowed_channels", "provider_id"}:
        raise ValueError("渠道选择格式不正确。")
    if "ai" in setup:
        if not isinstance(setup["ai"], dict):
            raise ValueError("API 配置须为对象。")
        if "allowed_channels" in overrides:
            setup["ai"]["allowed_channels"] = overrides["allowed_channels"]
        elif connections.get("channels"):
            names = connections["channels"]
            if not isinstance(names, list) or any(not isinstance(n, str) for n in names):
                raise ValueError("模型渠道名称须为列表。")
            matches = AIChannel.query.filter(AIChannel.name.in_(names)).all()
            if {item.name for item in matches} != set(names):
                raise ValueError("本平台没有包内的模型渠道，请重新选择模型渠道。")
            setup["ai"]["allowed_channels"] = [item.id for item in matches]
    if "compute" in setup:
        if not isinstance(setup["compute"], dict):
            raise ValueError("算力配置须为对象。")
        if "provider_id" in overrides:
            setup["compute"]["provider_id"] = overrides["provider_id"]
        elif connections.get("provider"):
            if not isinstance(connections["provider"], str):
                raise ValueError("算力渠道名称须为文本。")
            provider = ComputeProvider.query.filter_by(name=connections["provider"]).first()
            if not provider:
                raise ValueError("本平台没有包内的算力渠道，请重新选择算力渠道。")
            setup["compute"]["provider_id"] = provider.id
    return normalize_setup(setup)


def export_package(problem):
    data = deepcopy(setup_data(problem))
    connections = {}
    runtime = data.pop("runtime")
    if data["ai"] is None:
        data.pop("ai")
    else:
        ids = data["ai"].pop("allowed_channels", [])
        connections["channels"] = [c.name for c in AIChannel.query.filter(AIChannel.id.in_(ids)).all()] if ids else []
    if data["compute"] is None:
        data.pop("compute")
    else:
        provider = db.session.get(ComputeProvider, data["compute"].pop("provider_id"))
        connections["provider"] = provider.name
    from .routes.manage import PROBLEM_FIELDS
    document = {key: deepcopy(getattr(problem, key)) for key in PROBLEM_FIELDS if key not in {"statement_md", "evaluation_config"}}
    document["status"] = "draft"
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        def write(name, value):
            archive.writestr(name, json.dumps(value, ensure_ascii=False, indent=2))
        write("problem.json", {"version": 1, "problem": document, "setup": data})
        archive.writestr("statement.md", problem.statement_md)
        write("connections.json", connections)
        if runtime:
            scenes = runtime.pop("scenarios")
            write("runtime.json", runtime)
            write("scenarios.json", scenes)
        archive.writestr("README.md", "赛题包 version 1；导入后为草稿。渠道仅按名称绑定，不含密钥、SSH 密码或队伍用量。\n包内私有测试场景仅供组织方，请勿公开。\n")
    output.seek(0)
    return output
