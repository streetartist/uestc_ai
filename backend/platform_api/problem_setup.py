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
        raise ValueError("请先选择评测器，再设置运行环境。")
    if not isinstance(value, dict) or set(value) - {"image", "agent_image", "scenarios", "execution"}:
        raise ValueError("测评环境只接受运行方式、镜像、智能体镜像和测试场景。")
    value = deepcopy(value)
    native = value.get("execution") == "autodl-native"
    if "execution" in value and (not isinstance(value["execution"], str) or value["execution"] not in {"docker", "autodl-native"}):
        raise ValueError("未知测评运行方式。")
    if native and (evaluation["adapter"] != "classification-v1" or not evaluation["resources"]["gpu"]
                   or evaluation["api"]["enabled"]):
        raise ValueError("AutoDL 原生测评仅用于无需模型 API 的 GPU 分类推理。")
    for name in ("image", "agent_image"):
        image = value.get(name, "")
        if native and name == "image":
            if not isinstance(image, str) or not re.fullmatch(r"image-[A-Za-z0-9_-]{1,150}", image):
                raise ValueError("AutoDL 原生环境需要已保存的私有镜像编号 image-…。")
            continue
        if not isinstance(image, str) or (name == "image" or image) and not re.fullmatch(r"(?:[^\s]{1,250}@)?sha256:[a-f0-9]{64}", image):
            raise ValueError("Docker 镜像须填写固定 sha256 摘要，不能使用 latest 等可变标签。")
    scenes = value.get("scenarios", [])
    if not isinstance(scenes, list) or len(json.dumps(scenes, allow_nan=False).encode()) > 1024 * 1024:
        raise ValueError("私有测试场景须为 JSON 列表，且不超过 1 MB。")
    if native:
        if value.get("agent_image"):
            raise ValueError("AutoDL 原生分类环境不接受智能体镜像。")
        from evaluation_adapters.classification_runner import validate_scenarios
        validate_scenarios(scenes, evaluation["resources"]["episodes"])
    elif evaluation["adapter"] == "minecraft-agent-v1" and evaluation["task"] == "open-world":
        if not value.get("agent_image"):
            raise ValueError("MC 开放世界需要独立的智能体镜像。")
        from evaluation_adapters.minecraft_runner import validate_scenarios
        validate_scenarios(scenes, evaluation["resources"]["episodes"])
    elif evaluation["adapter"] in {"robot-arm-agent-v1", "libero-agent-v1"}:
        if not value.get("agent_image"):
            raise ValueError("机械臂需要独立的智能体镜像。")
        if evaluation["adapter"] == "libero-agent-v1":
            from evaluation_adapters.libero_runner import validate_scenarios
        else:
            from evaluation_adapters.robot_arm_runner import validate_scenarios
        validate_scenarios(scenes, evaluation["resources"]["episodes"])
    elif value.get("agent_image"):
        raise ValueError("独立智能体镜像只用于 MC 或机械臂评测器。")
    elif scenes and len(scenes) != evaluation["resources"]["episodes"]:
        raise ValueError("私有场景数量必须与每次测试的场景数量一致。")
    result = {"image": value["image"], "agent_image": value.get("agent_image", ""), "scenarios": scenes}
    if "execution" in value:
        result["execution"] = value["execution"]
    return result


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
            saved.config.get(key, "") == runtime.get(key, "") for key in ("image", "agent_image", "execution")
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
    from .performance_scoring import validate_problem_scoring
    validate_problem_scoring(problem.scoring_config or {}, evaluation, problem.judging_schema or {})
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
    from .judge import configured_pool, available
    data = setup_data(problem)
    evaluation, runtime, ai, compute = (data[key] for key in ("evaluation_config", "runtime", "ai", "compute"))
    checks = []
    def add(key, label, state, detail):
        checks.append({"key": key, "label": label, "state": state, "detail": detail})
    if evaluation:
        adapter = next((item for item in catalog() if item["id"] == evaluation["adapter"]), None)
        add("adapter", "评测器接入", "ready" if adapter and adapter["available"] else "blocked", "已启用评测器" if adapter and adapter["available"] else "管理员需要启用此评测器并配置测评端认证。")
        add("runtime", "测评环境", "ready" if runtime else "warning", "固定镜像与私有场景已保存" if runtime else "尚未设置题目环境，将使用运行端原有配置。")
        workers = EvaluationWorkerState.query.filter(EvaluationWorkerState.seen_at > utcnow() - timedelta(seconds=75)).all()
        from evaluation_resources import memory_fits
        compatible = any(evaluation["adapter"] in w.capabilities["adapters"]
            and memory_fits(w.capabilities, evaluation)
            and (not runtime or w.capabilities.get("managed_runtime"))
            and (not runtime or runtime.get("execution", "docker") in w.capabilities.get("execution_backends", ["docker"]))
            and (not runtime or "runtime_images" not in w.capabilities or runtime["image"] in w.capabilities["runtime_images"])
            and (not runtime or runtime.get("execution") != "autodl-native" or all(
                w.capabilities.get("dataset_manifests", {}).get(scene["dataset"]) == scene["manifest_sha256"]
                for scene in runtime["scenarios"]))
            and (runtime or evaluation["adapter"] in w.capabilities.get("legacy_adapters", w.capabilities["adapters"]))
            and (not evaluation["resources"]["gpu"] or w.capabilities["gpu"])
            and (not evaluation["api"]["enabled"] or w.capabilities.get("api_proxy", True)) for w in workers)
        pool = configured_pool(problem)
        automatic = available(pool, runtime)
        if pool and runtime and runtime.get("execution") == "autodl-native":
            from .judge import worker_ready
            compatible = pool.state == "ready" and worker_ready(pool)
        add("worker", "测评端与自动调度", "ready" if compatible or automatic else "blocked",
            "已发现可处理本题的在线测评端" if compatible else
            f"专用 GPU 测评机自动唤醒已配置；无任务 {pool.idle_seconds} 秒后自动关机。" if automatic else
            pool.error if pool and pool.error else "没有匹配的在线测评端或可用自动调度，请检查服务与数据。")
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
        empty = not ai["allowed_models"] or ai["max_tokens"] == 0 or ai["max_calls"] == 0 or ai.get("max_cost_micros") == 0 or required and evaluation["api"]["max_calls"] == 0
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
    if (problem.track.competition.config or {}).get("launch", {}).get("require_ready"):
        from datetime import timezone
        from .performance_scoring import validate_problem_scoring
        competition = problem.track.competition
        start, end = competition.starts_at, competition.ends_at
        if start and start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end and end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        now = utcnow()
        scheduled = bool(start and end and start < end)
        add("schedule", "比赛起止时间", "ready" if scheduled and start <= now < end else "blocked",
            "比赛进行中，截止后停止自测与提交。" if scheduled and start <= now < end else
            "尚未到开赛时间。" if scheduled and now < start else "比赛已截止。" if scheduled else "请在赛事设置填写开赛与截止时间。")
        if evaluation and not runtime:
            add("formal_runtime", "正式场景与数据", "blocked", "正式测评必须绑定固定镜像和私有测试场景。")
        try:
            validate_problem_scoring(problem.scoring_config or {}, evaluation or {}, problem.judging_schema or {})
            score_ready = bool((problem.scoring_config or {}).get("performance_scoring"))
        except ValueError:
            score_ready = False
        add("scoring", "正式计分规则", "ready" if score_ready else "blocked",
            "表现分由可信指标自动换算；报告和答辩独立评分。" if score_ready else "请配置与测评指标匹配的表现分公式和评分项。")
        if evaluation and evaluation["api"]["enabled"] and not (ai and ai["enabled"]):
            add("formal_api", "统一模型额度", "blocked", "正式赛题须提供本题统一模型和每队额度，不能仅使用运行端默认接口。")
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
        from .performance_scoring import validate_problem_scoring
        validate_problem_scoring(problem["scoring_config"], problem["evaluation_config"], problem.get("judging_schema", {}))
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
