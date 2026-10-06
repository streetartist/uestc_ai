from __future__ import annotations

import json
from copy import deepcopy

from flask import Blueprint, jsonify, request, send_file
from sqlalchemy.exc import IntegrityError

from ..ai_gateway import GatewayError
from ..extensions import db
from ..models import Competition, Problem, ProblemRuntime, Track
from ..problem_setup import bind_connections, export_package, read_package, readiness, runtime_images_allowed, save_setup, setup_data
from ..security import require_user
from ..utils import audit


setup_bp = Blueprint("problem_setup", __name__)


@setup_bp.errorhandler(ValueError)
@setup_bp.errorhandler(GatewayError)
def invalid_setup(error):
    db.session.rollback()
    return jsonify({"error": str(error)}), error.status if isinstance(error, GatewayError) else 400


@setup_bp.after_request
def no_cache(response):
    response.headers["Cache-Control"] = "no-store"
    return response


def find_problem(problem_id):
    problem = db.session.get(Problem, problem_id)
    if not problem:
        raise GatewayError("题目不存在。", 404)
    return problem


@setup_bp.get("/manage/problems/<problem_id>/setup")
@require_user("admin", "organizer")
def get_setup(problem_id):
    problem = find_problem(problem_id)
    from ..judge import configured_pool
    pool = configured_pool(problem)
    return jsonify({"config": setup_data(problem), "judge": pool.to_dict() if pool else None, **readiness(problem)})


@setup_bp.put("/manage/problems/<problem_id>/judge")
@require_user("admin")
def configure_judge(problem_id):
    """Bind an already prepared private instance, never create a paid instance."""
    import re
    from ..ai_quotas import lock_competition, lock_problem
    from ..compute_models import ComputeInstance, ComputeProvider
    from ..judge_models import JudgePool
    from ..judge import configured_pool, lock_pool
    from ..models import EvaluationRun
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) != {"provider_id", "remote_id", "worker_id", "enabled", "idle_seconds", "boot_seconds"}:
        raise ValueError("请完整填写专用测评实例与自动启停设置。")
    if (not isinstance(data["remote_id"], str) or not re.fullmatch(r"pro-[A-Za-z0-9_-]{1,150}", data["remote_id"])
            or not isinstance(data["worker_id"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", data["worker_id"])
            or not isinstance(data["provider_id"], str) or type(data["enabled"]) is not bool
            or type(data["idle_seconds"]) is not int or not 30 <= data["idle_seconds"] <= 600
            or type(data["boot_seconds"]) is not int or not 60 <= data["boot_seconds"] <= 1800):
        raise ValueError("实例编号或时长无效，空闲关机需 30—600 秒，开机等待需 60—1800 秒。")
    problem = find_problem(problem_id)
    lock_competition(problem.track.competition_id)
    lock_problem(problem.id)
    runtime = problem.runtime.config if problem.runtime else {}
    if runtime.get("execution") != "autodl-native" or problem.evaluation_config.get("adapter") != "classification-v1":
        raise ValueError("先为本题配置 AutoDL 原生分类测评镜像与测试数据。")
    provider = db.session.get(ComputeProvider, data["provider_id"])
    if not provider or not provider.enabled or not provider.secret:
        raise ValueError("请选择已启用且配置凭据的算力渠道。")
    if ComputeInstance.query.filter_by(remote_id=data["remote_id"]).first():
        raise ValueError("队伍训练实例不能用作组织方私有测评实例。")
    pool = configured_pool(problem)
    if pool:
        pool = lock_pool(pool.id)
        if pool.state not in {"off", "error"} or EvaluationRun.query.filter(
                EvaluationRun.judge_pool_id == pool.id, EvaluationRun.status.in_(["queued", "running"])).first():
            raise GatewayError("测评机正在运行或有待处理任务，关闭后再修改配置。", 409)
    else:
        pool = JudgePool(problem_id=problem.id)
        db.session.add(pool)
    for key, value in data.items():
        setattr(pool, key, value)
    pool.image = runtime["image"]
    pool.dataset_manifests = {s["dataset"]: s["manifest_sha256"] for s in runtime["scenarios"]}
    pool.checked_at, pool.error = None, None
    audit("evaluation.judge.configured", "problem", problem.id, {"remote_id": pool.remote_id})
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise GatewayError("实例或测评端标识已绑定其他题目。", 409) from None
    return jsonify(pool.to_dict())


@setup_bp.delete("/manage/problems/<problem_id>/judge")
@require_user("admin")
def remove_judge_binding(problem_id):
    from datetime import timedelta
    from sqlalchemy import update
    from ..ai_quotas import lock_competition, lock_problem
    from ..judge import configured_pool, lock_pool, aware
    from ..models import EvaluationRun, utcnow
    problem = find_problem(problem_id)
    lock_competition(problem.track.competition_id)
    lock_problem(problem.id)
    pool = configured_pool(problem)
    if not pool:
        return "", 204
    pool = lock_pool(pool.id)
    if (pool.enabled or pool.state != "off" or pool.provider_status not in {"shutdown", "stopped"}
            or not pool.checked_at or aware(pool.checked_at) < utcnow() - timedelta(seconds=75)
            or pool.lease_owner or EvaluationRun.query.filter(EvaluationRun.judge_pool_id == pool.id,
                EvaluationRun.status.in_(["queued", "running"])).first()):
        raise GatewayError("先停用自动调度，待平台确认实例已关机且没有待处理任务，再解除绑定。", 409)
    # Historical inputs and results remain immutable; only scheduling affinity
    # is detached. The remote instance and its disk are not released/deleted.
    db.session.execute(update(EvaluationRun).where(EvaluationRun.judge_pool_id == pool.id).values(judge_pool_id=None))
    audit("evaluation.judge.unbound", "problem", problem.id, {"remote_id": pool.remote_id})
    db.session.delete(pool)
    db.session.commit()
    return "", 204


@setup_bp.get("/manage/competitions/<competition_id>/launch-check")
@require_user("admin", "organizer")
def competition_launch_check(competition_id):
    competition = db.session.get(Competition, competition_id)
    if not competition:
        raise GatewayError("赛事不存在。", 404)
    problems = [{"id": p.id, "title": p.title, "slug": p.slug, **readiness(p)}
        for track in competition.tracks for p in track.problems]
    return jsonify({"competition_id": competition.id, "ready": bool(problems) and all(p["ready"] for p in problems), "problems": problems})


@setup_bp.put("/manage/problems/<problem_id>/setup")
@require_user("admin", "organizer")
def put_setup(problem_id):
    problem = find_problem(problem_id)
    save_setup(problem, request.get_json(silent=True))
    db.session.commit()
    return jsonify({"config": setup_data(problem), **readiness(problem)})


@setup_bp.get("/manage/problem-runtimes")
@require_user("admin", "organizer")
def runtime_options():
    # Cases stay private to their problem; this catalogue only reuses trusted images.
    return jsonify([{"problem_id": item.problem_id, "name": item.problem.title,
                     "adapter": item.problem.evaluation_config.get("adapter"),
                     "image": item.config["image"], "agent_image": item.config.get("agent_image", ""),
                     "execution": item.config.get("execution", "docker")}
                    for item in ProblemRuntime.query.order_by(ProblemRuntime.created_at).all()])


@setup_bp.get("/manage/problems/<problem_id>/package")
@require_user("admin", "organizer")
def download_package(problem_id):
    problem = find_problem(problem_id)
    audit("problem.package.exported", "problem", problem.id)
    db.session.commit()
    return send_file(export_package(problem), mimetype="application/zip", as_attachment=True,
                     download_name=problem.slug + ".zip")


def upload():
    file = request.files.get("file")
    if not file:
        raise ValueError("请选择赛题 ZIP 包。")
    raw = file.read(20 * 1024 * 1024 + 1)
    problem, setup, connections = read_package(raw)
    try:
        bindings = json.loads(request.form.get("bindings", "{}"))
    except json.JSONDecodeError as error:
        raise ValueError("渠道绑定格式错误。") from error
    setup = bind_connections(setup, connections, bindings)
    runtime_images_allowed(setup.get("runtime"), setup["evaluation_config"])
    return problem, setup, connections


@setup_bp.post("/manage/problem-packages/preview")
@require_user("admin", "organizer")
def preview_package():
    problem, setup, connections = upload()
    runtime = setup.get("runtime")
    return jsonify({"problem": problem, "setup": setup, "connections": connections,
                    "scene_count": len(runtime.get("scenarios", [])) if runtime else 0,
                    "message": "已校验赛题包。导入会创建草稿，不会开机、调用模型或运行容器。"})


@setup_bp.post("/manage/tracks/<track_id>/problem-packages")
@require_user("admin", "organizer")
def import_package(track_id):
    track = db.session.get(Track, track_id)
    if not track:
        raise GatewayError("赛道不存在。", 404)
    document, setup, _ = upload()
    if Problem.query.filter(Problem.track_id == track_id, (Problem.code == document["code"]) | (Problem.slug == document["slug"])).first():
        raise GatewayError("当前赛道已有相同编号或标识的题目，请修改包内配置。", 409)
    problem = Problem(track=track, **deepcopy(document))
    db.session.add(problem)
    try:
        db.session.flush()
        save_setup(problem, setup)
        audit("problem.package.imported", "problem", problem.id, {"track_id": track_id})
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise GatewayError("当前赛道已有相同编号或标识的题目。", 409) from None
    return jsonify(problem.to_dict(include_statement=True)), 201
