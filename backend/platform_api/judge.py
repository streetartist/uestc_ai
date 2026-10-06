"""Durable wake/drain/stop loop for registered organizer instances only.

External mutations follow a committed intent. An uncertain power-on is never
replayed: status is reconciled until the boot deadline, then the instance is
stopped and unstarted trials are refunded. No instance creation is supported.
"""
import base64
import json
import shlex
from datetime import timedelta, timezone
from pathlib import Path

from sqlalchemy import or_, update

from .ai_gateway import GatewayError, decrypt_keys
from .autodl import AutoDL
from .extensions import db
from .judge_models import JudgePool
from .models import EvaluationRun, EvaluationWorkerState, Submission, utcnow

LEASE_SECONDS = 120
MAX_UPTIME_SECONDS = 4 * 3600
STATE_LABELS = {"off": "等待自动开机", "starting": "测评机启动中", "ready": "等待测评端领取",
                "draining": "等待测评机重启", "stopping": "等待测评机关闭并重启", "error": "测评端暂不可用"}


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


def lock_pool(pool_id):
    db.session.execute(update(JudgePool).where(JudgePool.id == pool_id).values(id=JudgePool.id))
    return db.session.get(JudgePool, pool_id, populate_existing=True)


def configured_pool(problem):
    return JudgePool.query.filter_by(problem_id=problem.id).first()


def matches(pool, runtime):
    return bool(runtime and runtime.get("execution") == "autodl-native" and runtime.get("image") == pool.image
                and runtime.get("scenarios") and all(pool.dataset_manifests.get(scene.get("dataset")) ==
                    scene.get("manifest_sha256") for scene in runtime["scenarios"]))


def available(pool, runtime):
    if not pool or not pool.enabled or not matches(pool, runtime) or pool.error:
        return False
    if not pool.checked_at or aware(pool.checked_at) <= utcnow() - timedelta(seconds=75):
        return False
    if not pool.provider.enabled or not pool.provider.secret:
        return False
    try:
        return bool(decrypt_keys(pool.provider.secret))
    except GatewayError:
        return False


def worker_ready(pool):
    state = db.session.get(EvaluationWorkerState, pool.worker_id)
    if not state or aware(state.seen_at) <= utcnow() - timedelta(seconds=75):
        return False
    caps = state.capabilities
    return bool("classification-v1" in caps.get("adapters", []) and caps.get("gpu") and caps.get("managed_runtime")
        and "autodl-native" in caps.get("execution_backends", []) and pool.image in caps.get("runtime_images", [])
        and all(caps.get("dataset_manifests", {}).get(k) == v for k, v in pool.dataset_manifests.items()))


def run_dispatch_status(run):
    if run.status != "queued":
        return None
    if not run.judge_pool_id:
        from .evaluation_queue import docker_queue_status
        return docker_queue_status(run)
    pool = db.session.get(JudgePool, run.judge_pool_id)
    if not pool:
        return None
    return {"state": pool.state, "label": STATE_LABELS.get(pool.state, "等待自动调度"), "detail": pool.error}


def bind_run(run, problem):
    pool = configured_pool(problem)
    if pool and matches(pool, run.runtime_snapshot):
        lock_pool(pool.id)
        run.judge_pool_id = pool.id


def startup_command(pool):
    from flask import current_app
    root = Path(__file__).resolve().parents[1]
    overlay = {"classification-evidence-worker.py": (root.parent / "deploy/linux/classification-evidence-worker.py").read_text(encoding="utf-8"),
               "evaluation_evidence.py": (root / "evaluation_evidence.py").read_text(encoding="utf-8"),
               "evaluation_transport.py": (root / "evaluation_transport.py").read_text(encoding="utf-8")}
    encoded = base64.b64encode(json.dumps(overlay).encode()).decode()
    bootstrap = ("import base64,json;from pathlib import Path;"
        "p=Path('/root/autodl-tmp/uestc-evaluation/evidence-overlay');p.mkdir(parents=True,exist_ok=True);"
        f"d=json.loads(base64.b64decode('{encoded}'));"
        "[(p.joinpath(k).write_text(v,encoding='utf-8'),p.joinpath(k).chmod(384)) for k,v in d.items()]")
    worker = ("set -a; . /etc/uestc-classification.env; set +a; "
        f"export EVALUATION_WORKER_ID={shlex.quote(pool.worker_id)}; "
        f"export EVALUATION_API_ORIGIN_IP={shlex.quote(current_app.config.get('EVALUATION_API_ORIGIN_IP', ''))}; "
        "exec /opt/uestc-classification/runtime/bin/python /root/autodl-tmp/uestc-evaluation/evidence-overlay/classification-evidence-worker.py")
    # Independent hard cap protects against a complete website/API outage. The
    # scheduler drains at 3h so a 30min job finishes before this fallback.
    return (f"(sleep {MAX_UPTIME_SECONDS}; /usr/bin/shutdown) >/tmp/contest-auto-stop.log 2>&1 & "
        f"/opt/uestc-classification/runtime/bin/python -c {shlex.quote(bootstrap)} && "
        f"nohup bash -c {shlex.quote(worker)} >>/root/autodl-tmp/uestc-evaluation/worker.log 2>&1 & sleep 1")


def refund_unstarted(pool, error):
    """Call while holding the pool lock; claim holds this same lock."""
    runs = EvaluationRun.query.filter_by(judge_pool_id=pool.id, status="queued", attempts=0).all()
    for run in runs:
        if run.quota_refunded:
            continue
        run.status, run.error, run.finished_at = "failed", error, utcnow()
        if run.purpose == "trial":
            submission = run.submission_version.submission
            db.session.execute(update(Submission).where(Submission.id == submission.id,
                Submission.evaluation_runs_used > 0).values(evaluation_runs_used=Submission.evaluation_runs_used - 1))
            run.quota_refunded = True


def claim_pool(owner):
    now = utcnow()
    candidates = JudgePool.query.filter(or_(JudgePool.checked_at.is_(None),
        JudgePool.checked_at < now - timedelta(seconds=5)), or_(JudgePool.lease_expires_at.is_(None),
        JudgePool.lease_expires_at < now)).order_by(JudgePool.checked_at.asc()).all()
    for pool in candidates:
        result = db.session.execute(update(JudgePool).where(JudgePool.id == pool.id,
            or_(JudgePool.lease_expires_at.is_(None), JudgePool.lease_expires_at < now)).values(
                lease_owner=owner, lease_expires_at=now + timedelta(seconds=LEASE_SECONDS)).execution_options(synchronize_session=False))
        db.session.commit()
        if result.rowcount:
            return pool.id
    return None


def process_pool(pool_id, owner, api_factory=AutoDL):
    pool = lock_pool(pool_id)
    if pool.lease_owner != owner or aware(pool.lease_expires_at) <= utcnow():
        db.session.rollback()
        return
    db.session.commit()
    api = None
    try:
        api = api_factory(pool.provider)
        status = api.status(pool.remote_id)
        pool = lock_pool(pool_id)
        if pool.lease_owner != owner or aware(pool.lease_expires_at) <= utcnow():
            db.session.rollback()
            return
        now = utcnow()
        pool.checked_at, pool.provider_status = now, status
        pending = EvaluationRun.query.filter(EvaluationRun.judge_pool_id == pool.id,
            EvaluationRun.status.in_(["queued", "running"])).all()
        live = [r for r in pending if r.status == "running" and r.lease_expires_at and aware(r.lease_expires_at) > now]
        # Expired executions are reclaimable, never refunded as a boot failure.
        for run in pending:
            if run.status == "running" and not live and run.attempts >= 3:
                run.status, run.error, run.finished_at = "failed", "测评端多次中断，请联系组织方复核。", now
                run.lease_hash = run.api_token_hash = None
        pending = [r for r in pending if r.status in {"queued", "running"}]
        action = None
        if status in {"shutdown", "stopped"}:
            pool.idle_since = None
            if pool.state in {"starting"}:
                # Even a lost response followed by shutdown cannot replay a boot
                # request; wait until its deadline and fail once.
                if aware(pool.dispatched_at) + timedelta(seconds=pool.boot_seconds) <= now:
                    pool.error = "GPU 测评机未能开机（可能库存不足），测试尚未执行，次数已退回。"
                    refund_unstarted(pool, pool.error)
                    pool.state, pool.retry_at = "error", now + timedelta(seconds=60)
            elif pool.retry_at and aware(pool.retry_at) > now:
                pool.state = "error"
            else:
                pool.state, pool.error, pool.retry_at = "off", None, None
                if pending and pool.enabled and pool.provider.enabled:
                    command = startup_command(pool)
                    pool.state, pool.dispatched_at = "starting", now
                    action = ("power_on", command)
                elif pending and (not pool.enabled or not pool.provider.enabled):
                    refund_unstarted(pool, "组织方暂停了 GPU 测评，次数已退回。")
        elif status == "running":
            if pool.state in {"stopping", "error"}:
                pool.state = "stopping"
                action = ("power_off", None)
            elif worker_ready(pool):
                pool.error = None
                pool.dispatched_at = pool.dispatched_at or now
                if pool.state == "draining" or not pool.enabled or aware(pool.dispatched_at) + timedelta(seconds=3 * 3600) <= now:
                    pool.state = "draining"
                    if not live:
                        pool.state, action = "stopping", ("power_off", None)
                else:
                    pool.state = "ready"
                    pool.idle_since = None if pending else pool.idle_since or now
                    if pool.idle_since and aware(pool.idle_since) + timedelta(seconds=pool.idle_seconds) <= now:
                        pool.state, action = "stopping", ("power_off", None)
            else:
                pool.dispatched_at = pool.dispatched_at or now
                pool.state = "starting"
                if aware(pool.dispatched_at) + timedelta(seconds=pool.boot_seconds) <= now:
                    pool.error = "GPU 已启动但可信测评端未就绪，未执行的测试次数已退回。"
                    refund_unstarted(pool, pool.error)
                    pool.state, pool.retry_at, action = "stopping", now + timedelta(seconds=60), ("power_off", None)
        elif pool.dispatched_at and aware(pool.dispatched_at) + timedelta(seconds=pool.boot_seconds) <= now:
            pool.error = "AutoDL 状态长时间未确认，未执行的测试次数已退回，平台继续核对关机。"
            refund_unstarted(pool, pool.error)
            pool.state, pool.retry_at, action = "stopping", now + timedelta(seconds=60), ("power_off", None)
        db.session.commit()  # Intent and refunds precede any external mutation.
        if action:
            if action[0] == "power_on":
                api.request("power_on", {"instance_uuid": pool.remote_id, "payload": "gpu", "start_command": action[1]})
            else:
                api.power_off(pool.remote_id)
    except GatewayError as error:
        db.session.rollback()
        pool = lock_pool(pool_id)
        if pool.lease_owner == owner:
            pool.checked_at = utcnow()
            # Never store upstream responses or secrets.
            pool.error = "AutoDL 暂时无法确认状态，平台正在重试核对。"
            if error.code == "provider_rejected" and pool.state == "starting":
                pool.error = "AutoDL 拒绝开机，请组织方检查库存、余额和渠道；未执行次数已退回。"
                refund_unstarted(pool, pool.error)
                pool.state, pool.retry_at = "error", utcnow() + timedelta(seconds=60)
            # Status endpoint outages also have a bounded wait for unstarted jobs.
            oldest = EvaluationRun.query.filter_by(judge_pool_id=pool.id, status="queued", attempts=0).order_by(EvaluationRun.created_at).first()
            wait_since = pool.dispatched_at or (oldest.created_at if oldest else None)
            if wait_since and aware(wait_since) + timedelta(seconds=pool.boot_seconds) <= utcnow():
                refund_unstarted(pool, "测评基础设施长时间不可用，未执行测试次数已退回。")
            db.session.commit()
    finally:
        db.session.execute(update(JudgePool).where(JudgePool.id == pool_id, JudgePool.lease_owner == owner).values(
            lease_owner=None, lease_expires_at=None))
        db.session.commit()
