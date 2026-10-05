from __future__ import annotations

import json
from copy import deepcopy

from flask import Blueprint, jsonify, request, send_file
from sqlalchemy.exc import IntegrityError

from ..ai_gateway import GatewayError
from ..extensions import db
from ..models import Problem, ProblemRuntime, Track
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
    return jsonify({"config": setup_data(problem), **readiness(problem)})


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
                     "image": item.config["image"], "agent_image": item.config.get("agent_image", "")}
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
