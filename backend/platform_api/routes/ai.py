from __future__ import annotations

import json
import math
import secrets
import urllib.error
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request
from sqlalchemy import func, update
from sqlalchemy.exc import IntegrityError

from ..ai_gateway import (GatewayError, decrypt_keys, encrypt_keys, error_response, key_auth,
    model_catalog, proxy, upstream_request, validate_url)
from ..ai_models import AIChannel, AIGrant, AIKey, AIProblemQuota, AIUsage
from ..ai_quotas import apply_quota, lock_competition, lock_problem
from ..extensions import db
from ..models import CompetitionReviewer, Problem, Team, TeamMember, Track, utcnow
from ..security import current_user, hash_token, require_user, team_member
from ..utils import audit


ai_bp = Blueprint("ai", __name__)


@ai_bp.errorhandler(GatewayError)
def gateway_error(error):
    # Console uses the site's existing flat error convention; SDK uses OpenAI's.
    if "/v1/" in request.path:
        return error_response(error)
    return jsonify({"error": str(error), "code": error.code}), error.status


def data_object():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise GatewayError("请提交 JSON 对象。")
    return data


def integer(data, name, default, low=0, high=10**12):
    value = data.get(name, default)
    if type(value) is not int or not low <= value <= high:
        raise GatewayError(f"{name} 必须是 {low} 到 {high} 之间的整数。")
    return value


def grant_scope():
    user = current_user()
    query = AIGrant.query.join(Team)
    if user.role in {"admin", "organizer"}:
        return query
    if user.role == "reviewer":
        assigned = db.session.query(CompetitionReviewer.competition_id).filter_by(reviewer_id=user.id)
        return query.filter(Team.competition_id.in_(assigned))
    return query.join(TeamMember).filter(TeamMember.user_id == user.id)


def own_grant(grant_id):
    if not isinstance(grant_id, str):
        raise GatewayError("请选择所属队伍配额。")
    grant = db.session.get(AIGrant, grant_id)
    if not grant or not team_member(grant.team_id, current_user().id):
        raise GatewayError("只有本队成员可以管理本队 API 密钥。", 403, "forbidden")
    return grant


def close_stale_usage():
    # A dead worker must not leave an apparently live request forever. Keep its
    # reserve because the provider may have completed generation before a crash.
    db.session.execute(update(AIUsage).where(AIUsage.status == "pending", AIUsage.deadline < utcnow()).values(
        status="uncertain", error_code="worker_interrupted").execution_options(synchronize_session=False))
    db.session.commit()


@ai_bp.get("/ai/overview")
@require_user()
def overview():
    close_stale_usage()
    grants = grant_scope().order_by(AIGrant.created_at.desc()).all()
    ids = [grant.id for grant in grants]
    usage = AIUsage.query.filter(AIUsage.grant_id.in_(ids))
    count = usage.count()
    completed = usage.filter_by(status="completed").count()
    measured = db.session.query(func.coalesce(func.sum(AIUsage.input_tokens), 0),
        func.coalesce(func.sum(AIUsage.output_tokens), 0), func.avg(AIUsage.latency_ms)).filter(AIUsage.grant_id.in_(ids)).one()
    daily = db.session.query(func.date(AIUsage.created_at), func.count(AIUsage.id), func.sum(AIUsage.charged_tokens)).filter(
        AIUsage.grant_id.in_(ids)).group_by(func.date(AIUsage.created_at)).order_by(func.date(AIUsage.created_at).desc()).limit(14).all()
    own_ids = {row.team_id for row in TeamMember.query.filter_by(user_id=current_user().id)}
    return jsonify({"grants": [g.to_dict() for g in grants], "key_grant_ids": [g.id for g in grants if g.team_id in own_ids],
        "models": model_catalog([g for g in grants if g.enabled]),
        "summary": {"requests": count, "completed": completed, "input_tokens": measured[0], "output_tokens": measured[1],
            "charged_tokens": sum(g.tokens_used for g in grants), "cost_micros": sum(g.cost_used_micros for g in grants),
            "average_latency_ms": round(measured[2] or 0), "uncertain": usage.filter_by(status="uncertain").count(),
            "pending": usage.filter_by(status="pending").count()},
        "daily": [{"date": str(date), "requests": calls, "tokens": tokens} for date, calls, tokens in reversed(daily)]})


@ai_bp.get("/ai/keys")
@require_user()
def keys():
    user = current_user()
    own = db.session.query(AIGrant.id).join(TeamMember, TeamMember.team_id == AIGrant.team_id).filter(TeamMember.user_id == user.id)
    return jsonify([key.to_dict() for key in AIKey.query.filter(AIKey.grant_id.in_(own)).order_by(AIKey.created_at.desc()).all()])


@ai_bp.post("/ai/keys")
@require_user()
def create_key():
    data = data_object()
    grant = own_grant(data.get("grant_id"))
    name = data.get("name", "")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
        raise GatewayError("请填写 1 到 80 字的密钥名称。")
    if not grant.enabled:
        raise GatewayError("本队 API 配额已停用。", 403)
    if AIKey.query.filter_by(grant_id=grant.id, enabled=True).count() >= 20:
        raise GatewayError("每份配额最多同时启用 20 个密钥。")
    token = "sk-contest-" + secrets.token_urlsafe(32)
    key = AIKey(grant_id=grant.id, created_by=current_user().id, name=name.strip(), key_hash=hash_token(token), prefix=token[:18])
    db.session.add(key)
    db.session.flush()
    audit("ai.key_created", "ai_key", key.id)
    db.session.commit()
    return jsonify({**key.to_dict(), "token": token}), 201


@ai_bp.delete("/ai/keys/<key_id>")
@require_user()
def revoke_key(key_id):
    key = db.session.get(AIKey, key_id)
    if not key:
        raise GatewayError("密钥不存在。", 404)
    if current_user().role not in {"admin", "organizer"}:
        own_grant(key.grant_id)
    key.enabled = False
    audit("ai.key_revoked", "ai_key", key.id)
    db.session.commit()
    return jsonify(key.to_dict())


@ai_bp.get("/ai/usage")
@require_user()
def usage_logs():
    close_stale_usage()
    scope = grant_scope().with_entities(AIGrant.id)
    query = AIUsage.query.filter(AIUsage.grant_id.in_(scope))
    if problem_id := request.args.get("problem_id"):
        query = query.filter(AIUsage.grant_id.in_(db.session.query(AIGrant.id).filter_by(problem_id=problem_id)))
    for name in ("grant_id", "model", "channel_id", "key_id", "status", "evaluation_run_id"):
        if value := request.args.get(name):
            query = query.filter(getattr(AIUsage, name) == value)
    for name, compare in (("from", "ge"), ("to", "le")):
        if value := request.args.get(name):
            try:
                stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
                stamp = stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp
            except ValueError:
                raise GatewayError("日期格式无效。") from None
            query = query.filter(AIUsage.created_at >= stamp if compare == "ge" else AIUsage.created_at <= stamp)
    try:
        page, limit = int(request.args.get("page", 1)), int(request.args.get("limit", 50))
        if page < 1 or not 1 <= limit <= 200:
            raise ValueError()
    except ValueError:
        raise GatewayError("分页参数无效。") from None
    return jsonify({"items": [u.to_dict() for u in query.order_by(AIUsage.created_at.desc()).offset((page-1)*limit).limit(limit)],
                    "total": query.count(), "page": page, "limit": limit})


@ai_bp.get("/ai/manage/teams")
@require_user("admin", "organizer")
def teams():
    return jsonify([{"id": team.id, "name": team.name, "competition_id": team.competition_id, "competition_name": team.competition.name}
                    for team in Team.query.order_by(Team.created_at.desc()).all()])


@ai_bp.get("/ai/manage/problems")
@require_user("admin", "organizer")
def quota_problems():
    query = Problem.query.join(Track)
    if competition_id := request.args.get("competition_id"):
        query = query.filter(Track.competition_id == competition_id)
    return jsonify([{"id": p.id, "title": p.title, "code": p.code,
                     "competition_id": p.track.competition_id, "competition_name": p.track.competition.name}
                    for p in query.order_by(Problem.created_at.desc()).all()])


def quota_values(data):
    models = data.get("allowed_models")
    channel_ids = data.get("allowed_channels", [])
    if not isinstance(models, list) or not models or len(models) > 100 or any(not isinstance(m, str) or not 1 <= len(m) <= 160 for m in models):
        raise GatewayError("至少选择一个允许使用的模型。")
    if not isinstance(channel_ids, list) or any(not isinstance(c, str) or not db.session.get(AIChannel, c) for c in channel_ids):
        raise GatewayError("所选渠道无效。")
    if type(data.get("enabled", True)) is not bool:
        raise GatewayError("启用状态无效。")
    values = {name: integer(data, name, default, low, high) for name, default, low, high in (
        ("max_calls", 1000, 0, 10**8), ("max_tokens", 1000000, 0, 10**12),
        ("max_output_tokens", 4096, 1, 1000000), ("requests_per_minute", 60, 1, 10000), ("max_concurrent", 2, 1, 100))}
    budget = data.get("max_cost_micros")
    if budget is not None and (type(budget) is not int or not 0 <= budget <= 10**15):
        raise GatewayError("费用配额无效。")
    return {**values, "allowed_models": sorted(set(models)), "allowed_channels": list(dict.fromkeys(channel_ids)),
            "max_cost_micros": budget, "enabled": data.get("enabled", True)}


def quota_below_usage(config, grant):
    return (config["max_calls"] < grant.calls_used or config["max_tokens"] < grant.tokens_used or
            (config["max_cost_micros"] is not None and config["max_cost_micros"] < grant.cost_used_micros))


@ai_bp.get("/ai/manage/problems/<problem_id>/quota")
@require_user("admin", "organizer")
def problem_quota(problem_id):
    problem = db.session.get(Problem, problem_id)
    if not problem:
        raise GatewayError("题目不存在。", 404)
    policy = db.session.get(AIProblemQuota, problem_id)
    teams_count = Team.query.filter_by(competition_id=problem.track.competition_id).count()
    return jsonify({"problem_id": problem_id, "config": policy.config if policy else None, "team_count": teams_count})


@ai_bp.put("/ai/manage/problems/<problem_id>/quota")
@require_user("admin", "organizer")
def save_problem_quota(problem_id):
    config = quota_values(data_object())
    problem = db.session.get(Problem, problem_id)
    if not problem:
        raise GatewayError("题目不存在。", 404)
    team_count, created = apply_problem_quota(problem, config)
    db.session.commit()
    return jsonify({"problem_id": problem_id, "config": config, "team_count": team_count}), 201 if created else 200


def apply_problem_quota(problem, config):
    problem_id = problem.id
    lock_competition(problem.track.competition_id)
    lock_problem(problem_id)
    # Grant locks also serialize quota edits against concurrent reservations.
    grants = AIGrant.query.filter_by(problem_id=problem_id).order_by(AIGrant.id).with_for_update().all()
    if any(quota_below_usage(config, grant) for grant in grants):
        raise GatewayError("统一额度不能低于任何队伍的已用量，请提高额度；需要暂停时可关闭本题 API。", 409)
    policy = db.session.get(AIProblemQuota, problem_id, populate_existing=True)
    created = policy is None
    policy = policy or AIProblemQuota(problem_id=problem_id)
    policy.config = config
    db.session.add(policy)
    teams = Team.query.filter_by(competition_id=problem.track.competition_id).order_by(Team.id).all()
    existing = {grant.team_id: grant for grant in grants}
    for team in teams:
        grant = existing.get(team.id) or AIGrant(team_id=team.id, problem_id=problem_id)
        apply_quota(grant, config)
        db.session.add(grant)
    audit("ai.problem_quota_updated", "problem", problem_id, {"team_count": len(teams), **config})
    return len(teams), created


@ai_bp.put("/ai/manage/grants/<team_id>")
@require_user("admin", "organizer")
def save_grant(team_id):
    team = db.session.get(Team, team_id)
    if not team:
        raise GatewayError("队伍不存在。", 404)
    data = data_object()
    problem_id = data.get("problem_id")
    if problem_id is not None and (not isinstance(problem_id, str) or not problem_id):
        raise GatewayError("请选择有效题目。")
    problem = db.session.get(Problem, problem_id) if problem_id else None
    if problem_id and not problem:
        raise GatewayError("题目不存在。", 404)
    if problem and problem.track.competition_id != team.competition_id:
        raise GatewayError("题目与队伍必须属于同一场比赛。")
    if problem:
        lock_competition(team.competition_id)
        lock_problem(problem.id)
    if problem and db.session.get(AIProblemQuota, problem.id):
        raise GatewayError("本题使用统一额度，请在题目设置中修改。", 409)
    config = quota_values(data)
    grant = AIGrant.query.filter_by(team_id=team_id, problem_id=problem_id).first()
    created = grant is None
    grant = grant or AIGrant(team_id=team_id, problem_id=problem_id)
    if not created and quota_below_usage(config, grant):
        raise GatewayError("新配额不能低于已用量；可直接停用队伍。")
    apply_quota(grant, config)
    db.session.add(grant)
    try:
        db.session.flush()
        audit("ai.grant_updated", "ai_grant", grant.id, {"team_id": team_id, "problem_id": problem_id, **config})
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise GatewayError("该题目的队伍配额已被其他管理者创建，请刷新后编辑。", 409) from None
    return jsonify(grant.to_dict()), 201 if created else 200


@ai_bp.get("/ai/manage/channels")
@require_user("admin", "organizer")
def channels():
    return jsonify([c.to_dict() for c in AIChannel.query.order_by(AIChannel.priority.desc(), AIChannel.name).all()])


@ai_bp.post("/ai/manage/channels")
@ai_bp.patch("/ai/manage/channels/<channel_id>")
@require_user("admin")
def save_channel(channel_id=None):
    data = data_object()
    channel = db.session.get(AIChannel, channel_id) if channel_id else None
    if channel_id and not channel:
        raise GatewayError("渠道不存在。", 404)
    channel = channel or AIChannel()
    name = data.get("name", channel.name)
    url = data.get("base_url", channel.base_url)
    protocol = data.get("protocol", channel.protocol or "openai")
    models = data.get("models", channel.models)
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80 or not isinstance(url, str) or len(url) > 500:
        raise GatewayError("请填写渠道名称和上游地址。")
    validate_url(url)
    if protocol not in {"openai", "anthropic", "custom"}:
        raise GatewayError("渠道协议无效。")
    if not isinstance(models, dict) or len(models) > 1000 or any(not isinstance(k, str) or not isinstance(v, str) or
            not 1 <= len(k) <= 160 or not 1 <= len(v) <= 160 for k, v in models.items()):
        raise GatewayError("模型映射必须为 模型名称:上游模型名称。")
    disabled = data.get("disabled_models", channel.disabled_models or [])
    if not isinstance(disabled, list) or any(not isinstance(m, str) or m not in models for m in disabled):
        raise GatewayError("停用模型列表无效。")
    channel.disabled_models = list(dict.fromkeys(disabled))
    keys = data.get("api_keys")
    if keys is not None:
        if not isinstance(keys, list) or not 1 <= len(keys) <= 50 or any(not isinstance(k, str) or not k.strip() or len(k) > 4096 for k in keys):
            raise GatewayError("请填写上游密钥，每行一个。")
        channel.secrets = encrypt_keys([k.strip() for k in keys])
    if not channel.secrets:
        raise GatewayError("请填写上游密钥。")
    for field in ("input_price", "output_price"):
        value = data.get(field, getattr(channel, field) or 0)
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1000000:
            raise GatewayError("单价必须是非负数字。")
        setattr(channel, field, value)
    if type(data.get("enabled", channel.enabled if channel_id else True)) is not bool:
        raise GatewayError("启用状态无效。")
    channel.name, channel.base_url, channel.protocol, channel.models = name.strip(), url.rstrip("/"), protocol, models
    channel.enabled = data.get("enabled", channel.enabled if channel_id else True)
    channel.weight = integer(data, "weight", channel.weight or 1, 1, 10000)
    channel.priority = integer(data, "priority", channel.priority or 0, 0, 1000)
    db.session.add(channel)
    try:
        db.session.flush()
        audit("ai.channel_updated", "ai_channel", channel.id, {"name": channel.name})
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise GatewayError("渠道名称已存在。", 409) from None
    return jsonify(channel.to_dict()), 200 if channel_id else 201


@ai_bp.post("/ai/manage/channels/<channel_id>/test")
@require_user("admin")
def test_channel(channel_id):
    channel = db.session.get(AIChannel, channel_id)
    if not channel:
        raise GatewayError("渠道不存在。", 404)
    if channel.protocol == "custom":
        raise GatewayError("自定义完整地址不支持模型发现，请在对话测试中验证。")
    discovered, ok = [], False
    try:
        with upstream_request(channel, decrypt_keys(channel.secrets)[0], "models") as response:
            body = response.read(1024 * 1024 + 1)
            if len(body) > 1024 * 1024:
                raise ValueError()
            payload = json.loads(body)
            discovered = [m["id"] for m in payload.get("data", []) if isinstance(m, dict) and isinstance(m.get("id"), str)][:1000]
            ok = True
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        pass
    channel.last_test = {"ok": ok, "at": utcnow().isoformat(), "models": discovered}
    audit("ai.channel_tested", "ai_channel", channel.id, {"ok": ok})
    db.session.commit()
    return jsonify(channel.last_test)


@ai_bp.get("/ai/v1/models")
def sdk_models():
    key = key_auth()
    return jsonify({"object": "list", "data": model_catalog([key.grant])})


@ai_bp.post("/ai/v1/chat/completions")
@ai_bp.post("/ai/v1/responses")
@ai_bp.post("/ai/anthropic/v1/messages")
def sdk_proxy():
    key = key_auth()
    endpoint = "messages" if request.path.endswith("/messages") else "responses" if request.path.endswith("/responses") else "chat/completions"
    return proxy(key.grant, request.get_json(silent=True), endpoint, key=key)
