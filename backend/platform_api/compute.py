"""Durable AutoDL allocations, reservations, shutdown and crash recovery."""
from datetime import timedelta, timezone
from math import ceil
from uuid import uuid4

from sqlalchemy import or_, update

from .ai_gateway import GatewayError
from .ai_quotas import lock_problem
from .autodl import AutoDL
from .compute_models import (ComputeGrant, ComputeInstance, ComputePolicy, ComputeProvider,
                             ComputeSession, ComputeWorkerHeartbeat)
from .extensions import db
from .models import Problem, Track, utcnow

ACTIVE_STATES = ("queued", "creating", "starting", "running", "stopping", "uncertain")
STOPPED_STATES = {"stopped", "shutdown", "released"}
GRACE_SECONDS = 30


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


def worker_online():
    return ComputeWorkerHeartbeat.query.filter(ComputeWorkerHeartbeat.expires_at > utcnow()).first() is not None


def heartbeat(worker_id):
    entry = db.session.get(ComputeWorkerHeartbeat, worker_id) or ComputeWorkerHeartbeat(id=worker_id)
    entry.expires_at = utcnow() + timedelta(seconds=45)
    db.session.add(entry)
    db.session.commit()


def apply_config(grant, config):
    for name, value in config.items():
        setattr(grant, name, value)


def provision_compute(team):
    ids = db.session.query(ComputePolicy.problem_id).join(Problem).join(Track).filter(
        Track.competition_id == team.competition_id).order_by(ComputePolicy.problem_id).all()
    for (problem_id,) in ids:
        lock_problem(problem_id)
        policy = db.session.get(ComputePolicy, problem_id, populate_existing=True)
        if policy and not ComputeGrant.query.filter_by(team_id=team.id, problem_id=problem_id).first():
            grant = ComputeGrant(team_id=team.id, problem_id=problem_id)
            apply_config(grant, policy.config)
            db.session.add(grant)


def remove_unused_compute(team_id=None, problem_ids=None):
    query = ComputeGrant.query
    query = query.filter_by(team_id=team_id) if team_id else query.filter(ComputeGrant.problem_id.in_(problem_ids or []))
    grants = query.order_by(ComputeGrant.id).with_for_update().all()
    ids = [grant.id for grant in grants]
    if (ComputeInstance.query.filter(ComputeInstance.grant_id.in_(ids)).first()
            or ComputeSession.query.filter(ComputeSession.grant_id.in_(ids)).first()
            or any(grant.gpu_seconds_used or grant.cost_used_millis for grant in grants)):
        return False
    for grant in grants:
        db.session.delete(grant)
    db.session.flush()
    return True


def lock_grant(grant_id):
    db.session.execute(update(ComputeGrant).where(ComputeGrant.id == grant_id).values(
        gpu_seconds_used=ComputeGrant.gpu_seconds_used).execution_options(synchronize_session=False))
    return db.session.get(ComputeGrant, grant_id, populate_existing=True)


def reserve_start(grant_id, user_id, request_id, seconds):
    target = db.session.get(ComputeGrant, grant_id)
    lock_problem(target.problem_id)
    db.session.refresh(target)
    db.session.execute(update(ComputeProvider).where(ComputeProvider.id == target.provider_id).values(enabled=ComputeProvider.enabled))
    db.session.refresh(target.provider)
    grant = lock_grant(grant_id)
    existing = ComputeSession.query.filter_by(grant_id=grant.id, request_id=request_id).first()
    if existing:
        if existing.duration_seconds != seconds:
            raise GatewayError("同一开机请求不能更改使用时长。", 409)
        return existing, False
    if not worker_online():
        raise GatewayError("算力运行端尚未启动，暂时不能开机。", 503, "worker_offline")
    if not grant.enabled or not grant.provider.enabled:
        raise GatewayError("本题算力或渠道已停用。", 403)
    if ComputeSession.query.filter(ComputeSession.grant_id == grant.id, ComputeSession.state.in_(ACTIVE_STATES)).first():
        raise GatewayError("本队本题已有运行或待确认的实例，请先关机或等待核对。", 409)
    gpu_seconds = (seconds + GRACE_SECONDS) * grant.provider.gpu_count
    cost = max(10, ceil((seconds + GRACE_SECONDS) * grant.provider.hourly_price_millis / 3600))
    if (grant.gpu_seconds_used + gpu_seconds > grant.max_gpu_seconds
            or (grant.max_cost_millis is not None and grant.cost_used_millis + cost > grant.max_cost_millis)):
        raise GatewayError("本题算力额度不足，请缩短本次使用时长或联系组织方。", 429, "quota_exhausted")
    instance = ComputeInstance.query.filter_by(grant_id=grant.id).first()
    if instance and instance.provider_id != grant.provider_id:
        raise GatewayError("已有实例与渠道配置不一致，请联系组织方。", 409)
    if not instance:
        instance = ComputeInstance(grant_id=grant.id, provider_id=grant.provider_id, name="contest-" + uuid4().hex)
        db.session.add(instance)
        db.session.flush()
    grant.gpu_seconds_used += gpu_seconds
    grant.cost_used_millis += cost
    session = ComputeSession(grant_id=grant.id, instance_id=instance.id, request_id=request_id,
        requested_by=user_id, duration_seconds=seconds, gpu_count=grant.provider.gpu_count,
        rate_millis=grant.provider.hourly_price_millis, reserved_gpu_seconds=gpu_seconds,
        reserved_cost_millis=cost, next_check_at=utcnow())
    db.session.add(session)
    db.session.flush()
    return session, True


def finish(session, now, dispatched=True):
    # Timing is deliberately conservative: dispatch to confirmed shutdown,
    # multiplied by allocated cards. Provider invoices/storage fees are separate.
    owner = session.lease_owner
    grant = lock_grant(session.grant_id)
    db.session.refresh(session)
    if session.state not in ACTIVE_STATES or session.lease_owner != owner:
        db.session.rollback()
        return
    elapsed = max(0, ceil((now - aware(session.dispatched_at)).total_seconds())) if dispatched and session.dispatched_at else 0
    gpu_seconds = elapsed * session.gpu_count
    cost = max(10, ceil(elapsed * session.rate_millis / 3600)) if elapsed else 0
    db.session.execute(update(ComputeGrant).where(ComputeGrant.id == session.grant_id).values(
        gpu_seconds_used=ComputeGrant.gpu_seconds_used + gpu_seconds - session.reserved_gpu_seconds,
        cost_used_millis=ComputeGrant.cost_used_millis + cost - session.reserved_cost_millis))
    session.charged_gpu_seconds, session.charged_cost_millis = gpu_seconds, cost
    session.finished_at, session.state, session.lease_owner, session.lease_expires_at = now, "stopped", None, None
    grant = db.session.get(ComputeGrant, session.grant_id, populate_existing=True)
    if grant.gpu_seconds_used > grant.max_gpu_seconds or (grant.max_cost_millis is not None and grant.cost_used_millis > grant.max_cost_millis):
        grant.enabled = False
    db.session.commit()


def claim_session(worker_id):
    now = utcnow()
    candidates = ComputeSession.query.filter(ComputeSession.state.in_(ACTIVE_STATES), ComputeSession.next_check_at <= now,
        or_(ComputeSession.lease_expires_at.is_(None), ComputeSession.lease_expires_at <= now)).order_by(ComputeSession.next_check_at).limit(20).all()
    for candidate in candidates:
        changed = db.session.execute(update(ComputeSession).where(ComputeSession.id == candidate.id,
            ComputeSession.state.in_(ACTIVE_STATES),
            or_(ComputeSession.lease_expires_at.is_(None), ComputeSession.lease_expires_at <= now)).values(
                lease_owner=worker_id, lease_expires_at=now + timedelta(seconds=300)).execution_options(synchronize_session=False))
        db.session.commit()
        if changed.rowcount:
            return db.session.get(ComputeSession, candidate.id, populate_existing=True)
    return None


def process_session(session, worker_id):
    """No billable create or power_on is replayed after an ambiguous result."""
    now = utcnow()
    try:
        db.session.refresh(session)
        if session.lease_owner != worker_id or session.state not in ACTIVE_STATES:
            return
        grant = db.session.get(ComputeGrant, session.grant_id, populate_existing=True)
        if session.state == "queued" and (session.stop_requested or not grant.enabled or not session.instance.provider.enabled):
            finish(session, now, dispatched=False)
            return
        client = AutoDL(session.instance.provider)
        if session.state == "queued":
            session.dispatched_at, session.deadline = now, now + timedelta(seconds=session.duration_seconds)
            session.state = "starting" if session.instance.remote_id else "creating"
            # Persist before the external mutation, so a crash cannot replay it.
            db.session.commit()
            if session.instance.remote_id:
                client.power_on(session.instance.remote_id, session.duration_seconds)
            else:
                session.instance.remote_id = client.create(session.instance.provider, session.instance.name, session.duration_seconds)
                db.session.commit()
        elif not session.instance.remote_id:
            remote_id = client.find_created(session.instance.name)
            if not remote_id:
                session.state, session.error_code = "uncertain", "create_result_unknown"
                schedule(session)
                return
            session.instance.remote_id = remote_id
            db.session.commit()

        status = client.status(session.instance.remote_id)
        session.instance.provider_status = status
        # Successful reconciliation clears transient failures from earlier
        # queries. Retain shutdown causes for the usage record.
        if session.error_code not in {"price_unknown", "price_above_ceiling"}:
            session.error_code = None
        # Reload only mutable control fields: a web request may ask to stop while
        # the worker is waiting for an upstream response.
        db.session.refresh(session, ["stop_requested"])
        db.session.refresh(session, ["lease_owner"])
        if session.lease_owner != worker_id:
            db.session.rollback()
            return
        db.session.refresh(grant, ["enabled"])
        db.session.refresh(session.instance.provider, ["enabled"])
        should_stop = (session.stop_requested or not grant.enabled or not session.instance.provider.enabled
                       or (session.deadline and utcnow() >= aware(session.deadline)))
        if status in STOPPED_STATES:
            # A failed/slow startup is not evidence that a timed-out power_on
            # was never accepted. Observe until its deadline before settling.
            if session.state in {"starting", "creating", "uncertain"} and not should_stop:
                session.state = "uncertain"
                session.error_code = "startup_pending"
                schedule(session)
                return
            finish(session, utcnow())
            return
        if status == "running":
            snapshot = client.snapshot(session.instance.remote_id)
            price = snapshot.get("payg_price")
            if type(price) is not int or price <= 0:
                session.error_code, should_stop = "price_unknown", True
            elif price > session.rate_millis:
                session.rate_millis = price
                session.error_code, should_stop = "price_above_ceiling", True
            session.state = "running" if not should_stop else "stopping"
        elif not should_stop:
            session.state = "starting"
        if should_stop:
            session.stop_requested, session.state = True, "stopping"
            db.session.commit()
            client.power_off(session.instance.remote_id)
            session.instance.provider_status = client.status(session.instance.remote_id)
            if session.instance.provider_status in STOPPED_STATES:
                finish(session, utcnow())
                return
        schedule(session)
    except GatewayError as error:
        # Never refund unknown results and never include upstream secrets in logs.
        db.session.rollback()
        session = db.session.get(ComputeSession, session.id, populate_existing=True)
        if session.lease_owner != worker_id:
            return
        session.state = "uncertain" if session.dispatched_at else "queued"
        session.error_code = error.code
        schedule(session)


def schedule(session):
    session.next_check_at = utcnow() + timedelta(seconds=5 if session.stop_requested else 10)
    session.lease_owner = session.lease_expires_at = None
    db.session.commit()
