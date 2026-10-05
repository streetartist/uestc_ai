"""Uniform problem GPU budgets and team-private AutoDL Pro instances."""
import re

from flask import Blueprint, jsonify, redirect, request
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from ..ai_gateway import GatewayError, encrypt_keys
from ..ai_quotas import lock_competition, lock_problem
from ..autodl import AutoDL, validate_base
from ..compute import ACTIVE_STATES, apply_config, aware, reserve_start, worker_online
from ..compute_models import ComputeGrant, ComputeInstance, ComputePolicy, ComputeProvider, ComputeSession
from ..compute_catalog import SOURCE, GPU_SPECS, PUBLIC_IMAGES, DATA_CENTERS
from ..compute_tools import snapshot_tools
from ..extensions import db
from ..models import CompetitionReviewer, Problem, Team, TeamMember, new_id, utcnow
from ..security import current_user, require_user, team_member
from ..utils import audit
from .ai import data_object, integer

compute_bp = Blueprint("compute", __name__)


@compute_bp.errorhandler(GatewayError)
def compute_error(error):
    db.session.rollback()
    return jsonify({"error": str(error), "code": error.code}), error.status


def scope():
    user = current_user()
    query = ComputeGrant.query.join(Team)
    if user.role in {"admin", "organizer"}:
        return query
    if user.role == "reviewer":
        assigned = db.session.query(CompetitionReviewer.competition_id).filter_by(reviewer_id=user.id)
        return query.filter(Team.competition_id.in_(assigned))
    return query.join(TeamMember).filter(TeamMember.user_id == user.id)


def control(grant_id):
    grant = db.session.get(ComputeGrant, grant_id)
    user = current_user()
    if not grant or user.role == "reviewer" or (user.role not in {"admin", "organizer"} and not team_member(grant.team_id, user.id)):
        raise GatewayError("只能操作本队所属题目的算力实例。", 403, "forbidden")
    return grant


def text(data, name, default="", maximum=160):
    value = data.get(name, default)
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise GatewayError(f"请填写有效的 {name}。")
    return value.strip()


def boolean(data, name, default=True):
    value = data.get(name, default)
    if type(value) is not bool:
        raise GatewayError(f"{name} 必须是布尔值。")
    return value


@compute_bp.get("/compute/overview")
@require_user()
def overview():
    grants = scope().order_by(ComputeGrant.created_at.desc()).all()
    ids = [grant.id for grant in grants]
    return jsonify({"grants": [grant.to_dict() for grant in grants],
        "instances": [i.to_dict() for i in ComputeInstance.query.filter(ComputeInstance.grant_id.in_(ids)).all()],
        "sessions": [s.to_dict() for s in ComputeSession.query.filter(ComputeSession.grant_id.in_(ids)).order_by(ComputeSession.created_at.desc()).limit(100).all()],
        "active_sessions": [s.to_dict() for s in ComputeSession.query.filter(ComputeSession.grant_id.in_(ids), ComputeSession.state.in_(ACTIVE_STATES)).all()],
        "worker_online": worker_online(),
        "summary": {"gpu_seconds": sum(g.gpu_seconds_used for g in grants), "cost_millis": sum(g.cost_used_millis for g in grants)}})


@compute_bp.get("/compute/manage/providers")
@require_user("admin", "organizer")
def providers():
    return jsonify([p.to_dict() for p in ComputeProvider.query.order_by(ComputeProvider.name).all()])


@compute_bp.get("/compute/manage/options")
@require_user("admin", "organizer")
def compute_options():
    return jsonify({"gpu_specs": GPU_SPECS, "public_images": PUBLIC_IMAGES,
        "data_centers": DATA_CENTERS, "source": SOURCE, "verified_at": "2026-10-05"})


@compute_bp.post("/compute/manage/private-images")
@require_user("admin")
def private_images():
    data = data_object()
    token = data.get("token", "")
    if not isinstance(token, str) or len(token) > 4096 or any(ord(c) < 32 for c in token):
        raise GatewayError("AutoDL Token 格式无效。")
    provider_id = data.get("provider_id")
    if provider_id is not None and (not isinstance(provider_id, str) or len(provider_id) > 36):
        raise GatewayError("算力渠道编号无效。")
    provider = db.session.get(ComputeProvider, provider_id) if provider_id else None
    if provider_id and not provider:
        raise GatewayError("算力渠道不存在。", 404)
    if token.strip():
        # Unsaved credentials remain transient; never update the saved channel
        # when the administrator only wants to inspect available images.
        provider = ComputeProvider(base_url=provider.base_url if provider else "https://api.autodl.com",
            secret=encrypt_keys([token.strip()]))
    if not provider:
        raise GatewayError("请先填写 AutoDL Token，再读取私有镜像。")
    page = integer(data, "page", 1, 1, 10000)
    result = AutoDL(provider).list_page(page=page, page_size=100, action="image/private/list")
    maximum = result.get("max_page", page)
    if type(maximum) is not int or maximum < 0:
        raise GatewayError("AutoDL 分页数据无效。", 502, "provider_invalid_result")
    images = []
    for row in result["list"]:
        if not isinstance(row, dict) or not isinstance(row.get("image_uuid"), str):
            raise GatewayError("AutoDL 镜像数据无效。", 502, "provider_invalid_result")
        image_id = row["image_uuid"]
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", image_id):
            raise GatewayError("AutoDL 镜像编号无效。", 502, "provider_invalid_result")
        name = row.get("name")
        images.append({"id": image_id, "name": name[:200] if isinstance(name, str) and name.strip() else image_id,
            "ready": row.get("status") == "finished"})
    response = jsonify({"images": images, "page": page, "has_more": page < maximum})
    response.headers["Cache-Control"] = "no-store"
    return response


@compute_bp.post("/compute/manage/providers")
@compute_bp.put("/compute/manage/providers/<provider_id>")
@require_user("admin")
def save_provider(provider_id=None):
    data = data_object()
    provider = db.session.get(ComputeProvider, provider_id) if provider_id else ComputeProvider(id=new_id())
    if provider_id and not provider:
        raise GatewayError("算力渠道不存在。", 404)
    config = {"name": text(data, "name", provider.name, 80), "base_url": text(data, "base_url", provider.base_url or "https://api.autodl.com", 300),
        "image_uuid": text(data, "image_uuid", provider.image_uuid), "gpu_spec_uuid": text(data, "gpu_spec_uuid", provider.gpu_spec_uuid, 80),
        "gpu_label": text(data, "gpu_label", provider.gpu_label, 120), "enabled": boolean(data, "enabled", provider.enabled if provider_id else True),
        "gpu_count": integer(data, "gpu_count", provider.gpu_count or 1, 1, 4),
        "hourly_price_millis": integer(data, "hourly_price_millis", provider.hourly_price_millis, 1, 10**8),
        "cuda_v_from": integer(data, "cuda_v_from", provider.cuda_v_from or 113, 100, 999)}
    validate_base(config["base_url"])
    centers = data.get("data_centers", provider.data_centers or [])
    if not isinstance(centers, list) or len(centers) > 20 or any(not isinstance(c, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", c) for c in centers):
        raise GatewayError("数据中心列表格式无效。")
    config["data_centers"] = centers
    token = data.get("token", "")
    if not isinstance(token, str) or len(token) > 4096 or any(ord(c) < 32 for c in token):
        raise GatewayError("AutoDL Token 格式无效。")
    if not token.strip() and not provider.secret:
        raise GatewayError("首次保存必须填写 AutoDL 开发者 Token。")
    if provider_id:
        # Serializes edits with quota changes and start reservations.
        db.session.execute(update(ComputeProvider).where(ComputeProvider.id == provider_id).values(enabled=ComputeProvider.enabled))
        db.session.refresh(provider)
        if ComputeInstance.query.filter_by(provider_id=provider_id).first() and any(config[name] != getattr(provider, name) for name in ("base_url", "image_uuid", "gpu_spec_uuid", "gpu_count", "cuda_v_from", "data_centers")):
            raise GatewayError("渠道已有队伍实例，镜像、卡型和卡数不能更改；请另建渠道。", 409)
    for name, value in config.items():
        setattr(provider, name, value)
    if token.strip():
        provider.secret = encrypt_keys([token.strip()])
    db.session.add(provider)
    audit("compute.provider.saved", "compute_provider", provider.id, {"name": provider.name})
    try:
        db.session.commit()
    except IntegrityError:
        raise GatewayError("算力渠道名称已存在。", 409) from None
    return jsonify(provider.to_dict()), 200 if provider_id else 201


@compute_bp.post("/compute/manage/providers/<provider_id>/test")
@require_user("admin")
def test_provider(provider_id):
    provider = db.session.get(ComputeProvider, provider_id)
    if not provider:
        raise GatewayError("算力渠道不存在。", 404)
    AutoDL(provider).list_page(page_size=1)
    return jsonify({"ok": True})


@compute_bp.get("/compute/manage/problems/<problem_id>/quota")
@compute_bp.put("/compute/manage/problems/<problem_id>/quota")
@require_user("admin", "organizer")
def problem_quota(problem_id):
    problem = db.session.get(Problem, problem_id)
    if not problem:
        raise GatewayError("题目不存在。", 404)
    policy = db.session.get(ComputePolicy, problem_id)
    if request.method == "GET":
        return jsonify({"config": policy.config if policy else None, "team_count": Team.query.filter_by(competition_id=problem.track.competition_id).count()})
    data = data_object()
    provider_id = text(data, "provider_id", maximum=36)
    config = {"provider_id": provider_id, "enabled": boolean(data, "enabled"),
        "max_gpu_seconds": integer(data, "max_gpu_seconds", None, 0),
        "max_cost_millis": None if data.get("max_cost_millis") is None else integer(data, "max_cost_millis", None)}
    lock_competition(problem.track.competition_id)
    lock_problem(problem_id)
    db.session.execute(update(ComputeProvider).where(ComputeProvider.id == provider_id).values(enabled=ComputeProvider.enabled))
    provider = db.session.get(ComputeProvider, provider_id)
    if not provider:
        raise GatewayError("请选择有效的算力渠道。")
    grants = ComputeGrant.query.filter_by(problem_id=problem_id).order_by(ComputeGrant.id).with_for_update().all()
    for grant in grants:
        if grant.gpu_seconds_used > config["max_gpu_seconds"] or (config["max_cost_millis"] is not None and grant.cost_used_millis > config["max_cost_millis"]):
            raise GatewayError("新额度低于部分队伍已用或预占的额度，未保存任何修改。", 409)
        if grant.provider_id != provider_id and ComputeInstance.query.filter_by(grant_id=grant.id).first():
            raise GatewayError("已有队伍实例，不能更换本题渠道。", 409)
    by_team = {g.team_id: g for g in grants}
    for team in Team.query.filter_by(competition_id=problem.track.competition_id).order_by(Team.id).all():
        grant = by_team.get(team.id) or ComputeGrant(team_id=team.id, problem_id=problem_id)
        apply_config(grant, config)
        db.session.add(grant)
    policy = db.session.get(ComputePolicy, problem_id, populate_existing=True) or ComputePolicy(problem_id=problem_id)
    policy.config = config
    db.session.add(policy)
    audit("compute.problem.quota", "problem", problem_id, config)
    db.session.commit()
    return jsonify({"config": config, "team_count": Team.query.filter_by(competition_id=problem.track.competition_id).count()})


@compute_bp.post("/compute/grants/<grant_id>/start")
@require_user()
def start(grant_id):
    control(grant_id)
    data = data_object()
    request_id = text(data, "request_id", maximum=80)
    if not re.fullmatch(r"[A-Za-z0-9_-]+", request_id):
        raise GatewayError("开机请求编号无效。")
    seconds = integer(data, "duration_seconds", None, 60, 7 * 86400)
    session, created = reserve_start(grant_id, current_user().id, request_id, seconds)
    audit("compute.start.queued", "compute_session", session.id)
    db.session.commit()
    return jsonify(session.to_dict()), 202 if created else 200


@compute_bp.post("/compute/instances/<instance_id>/stop")
@require_user()
def stop(instance_id):
    instance = db.session.get(ComputeInstance, instance_id)
    if not instance:
        raise GatewayError("实例不存在。", 404)
    control(instance.grant_id)
    db.session.execute(update(ComputeSession).where(ComputeSession.instance_id == instance.id, ComputeSession.state.in_(ACTIVE_STATES)).values(stop_requested=True, next_check_at=utcnow()))
    audit("compute.stop.requested", "compute_instance", instance.id)
    db.session.commit()
    return jsonify({"ok": True, "worker_online": worker_online()})


@compute_bp.post("/compute/instances/<instance_id>/ssh")
@require_user()
def ssh(instance_id):
    instance = private_running_instance(instance_id)
    snapshot = AutoDL(instance.provider).snapshot(instance.remote_id)
    host, port, password = snapshot.get("proxy_host"), snapshot.get("ssh_port"), snapshot.get("root_password")
    if not isinstance(host, str) or not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", host) or type(port) is not int or not 1 <= port <= 65535 or not isinstance(password, str):
        raise GatewayError("AutoDL 尚未提供完整 SSH 信息。", 502)
    response = jsonify({"host": host, "port": port, "username": "root", "password": password, "command": f"ssh -p {port} root@{host}"})
    response.headers["Cache-Control"] = "no-store"
    return response


def private_running_instance(instance_id):
    instance = db.session.get(ComputeInstance, instance_id)
    if not instance:
        raise GatewayError("实例不存在。", 404)
    # Tool access is as private as SSH: current team members and admins only.
    user = current_user()
    if user.role == "reviewer" or (user.role != "admin" and not team_member(instance.grant.team_id, user.id)):
        raise GatewayError("仅本队成员与管理员可访问实例工具。", 403, "forbidden")
    active = ComputeSession.query.filter_by(instance_id=instance.id, state="running", stop_requested=False).first()
    if (not active or not instance.remote_id or not instance.grant.enabled or not instance.provider.enabled
            or (active.deadline and aware(active.deadline) <= utcnow())):
        raise GatewayError("实例尚未运行或正在关机。", 409)
    return instance


def live_tools(instance_id):
    instance = private_running_instance(instance_id)
    client = AutoDL(instance.provider)
    if client.status(instance.remote_id) != "running":
        raise GatewayError("实例尚未运行或正在关机，请刷新状态。", 409)
    tools = snapshot_tools(client.snapshot(instance.remote_id))
    # A slow query must not grant access after the deadline or a stop request.
    db.session.expire_all()
    private_running_instance(instance_id)
    return tools


@compute_bp.post("/compute/instances/<instance_id>/tools")
@require_user()
def instance_tools(instance_id):
    tools = live_tools(instance_id)
    response = jsonify({"available": {"jupyter": bool(tools["jupyter_url"]), "autopanel": bool(tools["autopanel_url"])},
        "services": tools["services"], "monitor": tools["monitor"]})
    response.headers["Cache-Control"] = "no-store"
    return response


@compute_bp.get("/compute/instances/<instance_id>/tools/<tool>")
@require_user()
def launch_tool(instance_id, tool):
    if tool not in {"jupyter", "autopanel"}:
        raise GatewayError("实例工具不存在。", 404)
    url = live_tools(instance_id)[tool + "_url"]
    if not url:
        raise GatewayError("AutoDL 尚未提供此工具的可用地址。", 409)
    response = redirect(url)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response
