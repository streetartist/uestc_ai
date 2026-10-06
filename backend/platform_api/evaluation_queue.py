"""Worker slot locking and private queue feedback across Docker adapters."""
from datetime import timedelta, timezone

from flask import g, has_request_context
from sqlalchemy import update
from evaluation_resources import memory_fits

from .extensions import db
from .models import EvaluationRun, EvaluationWorkerState, utcnow


def lock_worker(worker_id):
    db.session.execute(update(EvaluationWorkerState).where(EvaluationWorkerState.id == worker_id).values(id=EvaluationWorkerState.id))
    return db.session.get(EvaluationWorkerState, worker_id, populate_existing=True)


def occupied(worker_id, now):
    return EvaluationRun.query.filter(EvaluationRun.worker_id == worker_id,
        EvaluationRun.status == "running", EvaluationRun.lease_expires_at > now).first()


def supports(caps, run):
    config, runtime = run.config_snapshot, run.runtime_snapshot or {}
    adapter = config["adapter"]
    return bool(adapter in caps.get("adapters", [])
        and memory_fits(caps, config)
        and runtime.get("execution", "docker") in caps.get("execution_backends", ["docker"])
        and (not config["resources"]["gpu"] or caps.get("gpu"))
        and (not config["api"]["enabled"] or caps.get("api_proxy", True))
        and (not runtime or caps.get("managed_runtime"))
        and (not runtime or "runtime_images" not in caps or runtime["image"] in caps["runtime_images"])
        and (runtime or adapter in caps.get("legacy_adapters", caps.get("adapters", []))))


def docker_queue_status(run):
    if run.status != "queued" or run.judge_pool_id or (run.runtime_snapshot or {}).get("execution", "docker") != "docker":
        return None
    cached = getattr(g, "evaluation_docker_queue", None) if has_request_context() else None
    if cached is None:
        workers = EvaluationWorkerState.query.filter(EvaluationWorkerState.seen_at > utcnow() - timedelta(seconds=75)).all()
        pending = EvaluationRun.query.filter(EvaluationRun.judge_pool_id.is_(None),
            EvaluationRun.status.in_(["queued", "running"])).all()
        cached = (workers, pending)
        if has_request_context():
            g.evaluation_docker_queue = cached
    workers, pending = cached
    nodes = [w for w in workers if supports(w.capabilities, run)]
    if not nodes:
        return {"state": "waiting_worker", "label": "等待兼容的 Docker 测评节点", "detail": "任务保留在队列，具备对应环境与足够内存的节点上线后自动领取；等待不计入运行时限。", "ahead": None}
    # This is the number of earlier compatible pending jobs, not other teams'
    # identities or a promised start time. Nodes with independent capacity can
    # process multiple earlier jobs at the same time.
    def order(item):
        created = item.created_at
        return (created.replace(tzinfo=timezone.utc) if created.tzinfo is None else created, item.id)
    ahead = sum(1 for other in pending if other.id != run.id and order(other) < order(run)
        and any(supports(w.capabilities, other) for w in nodes))
    return {"state": "docker_queued", "label": f"Docker 测评排队中 · 前方 {ahead} 个待处理任务",
            "detail": "按入队时间领取，每个节点同时运行一项测试；等待不计入运行时限。", "ahead": ahead}
