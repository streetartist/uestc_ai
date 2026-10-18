from __future__ import annotations

import hmac
import json
import re
import secrets
import time
import urllib.error
import urllib.request
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request, send_from_directory
from sqlalchemy import and_, case, or_, update
from sqlalchemy.exc import IntegrityError

from ..evaluation import adapters, catalog, evaluation_budget, validate_metrics, validate_evaluation_config
from ..evaluation_trials import can_view_result, package_digest
from ..final_evaluation import required as final_required, digest, scene_identity, final_run, api_policy, validate_api_body
from ..final_evaluation import LIBERO_FINAL_WEIGHTS, validate_libero_final_scenes
from ..extensions import db
from ..models import EvaluationRun, EvaluationWorkerState, ProblemRuntime, Problem, Submission, SubmissionAsset, SubmissionVersion, Team, iso
from ..security import current_user, hash_token, require_user, team_member
from ..utils import audit


evaluations_bp = Blueprint("evaluations", __name__)
LEASE_SECONDS = 300
MAX_ATTEMPTS = 3


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


def now_utc():
    return datetime.now(timezone.utc)


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


def worker_authorized() -> bool:
    configured = current_app.config["EVALUATION_WORKER_TOKEN"]
    supplied = request.headers.get("Authorization", "")
    return bool(configured) and supplied.startswith("Bearer ") and hmac.compare_digest(configured, supplied[7:])


def leased_run(run_id: str):
    run = db.session.get(EvaluationRun, run_id)
    token = request.headers.get("X-Evaluation-Lease", "")
    if not run or not token or not run.lease_hash or not hmac.compare_digest(run.lease_hash, hash_token(token)):
        return None
    if run.status != "running" or not run.lease_expires_at or aware(run.lease_expires_at) <= now_utc():
        return None
    return run


@evaluations_bp.get("/evaluation-adapters")
@require_user("admin", "organizer")
def list_adapters():
    return jsonify(catalog())


@evaluations_bp.get("/problems/<problem_id>/evaluation-budget")
@require_user()
def team_budget(problem_id):
    team_id = request.args.get("team_id")
    user = current_user()
    if not team_id:
        return jsonify({"error": "team_id is required"}), 400
    if user.role not in {"admin", "organizer"} and not team_member(team_id, user.id):
        return jsonify({"error": "team membership required"}), 403
    problem = db.session.get(Problem, problem_id)
    team = db.session.get(Team, team_id)
    if not problem or not team:
        return jsonify({"error": "problem or team not found"}), 404
    if team.competition_id != problem.track.competition_id:
        return jsonify({"error": "team belongs to a different competition"}), 400
    submission = Submission.query.filter_by(problem_id=problem.id, team_id=team.id).first()
    response = jsonify({"problem_id": problem.id, "team_id": team.id, **evaluation_budget(problem, submission)})
    response.headers["Cache-Control"] = "no-store"
    return response


@evaluations_bp.get("/evaluation-runs/<run_id>")
@require_user()
def run_detail(run_id: str):
    run = db.session.get(EvaluationRun, run_id)
    user = current_user()
    if not run:
        return jsonify({"error": "evaluation run not found"}), 404
    if not can_view_result(run):
        return jsonify({"error": "insufficient permissions"}), 403
    return jsonify(run.to_dict())


@evaluations_bp.get("/submission-versions/<version_id>/evaluation-runs")
@require_user()
def version_runs(version_id: str):
    version = db.session.get(SubmissionVersion, version_id)
    user = current_user()
    if not version:
        return jsonify({"error": "submission version not found"}), 404
    if user.role not in {"admin", "organizer"} and not team_member(version.submission.team_id, user.id):
        return jsonify({"error": "insufficient permissions"}), 403
    runs = EvaluationRun.query.filter_by(submission_version_id=version_id).order_by(EvaluationRun.created_at.desc()).all()
    return jsonify([run.to_dict() for run in runs if can_view_result(run)])


@evaluations_bp.post("/submission-versions/<version_id>/final-evaluation-runs")
@require_user("admin", "organizer")
def start_final(version_id):
    from ..ai_quotas import lock_problem
    from ..problem_setup import validate_runtime, runtime_images_allowed
    from ..ai_gateway import GatewayError
    version = db.session.get(SubmissionVersion, version_id)
    if not version:
        return jsonify({"error": "submission version not found"}), 404
    problem = version.submission.problem
    lock_problem(problem.id)
    db.session.refresh(version)
    if not final_required(problem) or version.status != "submitted":
        return jsonify({"error": "请提交 LIBERO 作品并启用独立最终评测。"}), 409
    deadline = problem.track.competition.ends_at
    if not deadline or aware(deadline) > now_utc():
        return jsonify({"error": "最终评测在提交截止后启动。"}), 409
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) != {"scene_set_version", "runtime", "evaluation_config"}:
        return jsonify({"error": "请提供场景集版本、独立运行配置和最终评测预算。"}), 400
    label = data["scene_set_version"]
    if not isinstance(label, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", label):
        return jsonify({"error": "invalid scene_set_version"}), 400
    try:
        config = validate_evaluation_config(data["evaluation_config"])
        if any(config.get(k) != problem.evaluation_config.get(k) for k in ("adapter", "task", "metrics")):
            raise ValueError("最终评测须沿用已公布的评测器、任务类型和指标。")
        runtime = validate_runtime(data["runtime"], config)
        if not runtime or not runtime.get("scenarios"):
            raise ValueError("最终评测需要独立的私有场景配置。")
        libero_v2 = (problem.scoring_config or {}).get("performance_scoring", {}).get("preset") == "wujie-libero-v2"
        runtime_images_allowed(runtime, config)
        final_api = api_policy() if config["api"]["enabled"] else None
        public_scenes = (problem.runtime.config if problem.runtime else {}).get("scenarios", [])
        trial_runtimes = EvaluationRun.query.filter_by(problem_id=problem.id, purpose="trial").all()
        prior_scenes = [*public_scenes, *(s for r in trial_runtimes for s in (r.runtime_snapshot or {}).get("scenarios", []))]
        development = {scene_identity(s) for s in prior_scenes}
        if libero_v2:
            validate_libero_final_scenes(runtime["scenarios"], prior_scenes)
        elif any(scene_identity(s) in development for s in runtime["scenarios"]):
            raise ValueError("最终评测场景与开发自测重复；更换标签或 seed 仍使用相同初态。")
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    except GatewayError as error:
        return jsonify({"error": str(error)}), 403
    if config["api"]["enabled"] and (not current_app.config.get("EVALUATION_API_KEY")
            or not current_app.config.get("EVALUATION_API_URL", "").startswith("https://")):
        return jsonify({"error": "请配置组织方最终评测 API 通道；最终评测不消耗参赛队伍额度。"}), 503
    set_hash = digest(runtime)
    config_hash = digest({"evaluation_config": config, "api_policy": final_api})
    previous = EvaluationRun.query.filter_by(problem_id=problem.id, purpose="final").first()
    if previous and any(previous.submission_snapshot.get(k) != v for k, v in {
        "scene_set_version": label, "scene_set_sha256": set_hash, "final_config_sha256": config_hash}.items()):
        return jsonify({"error": "同一赛题的最终评测须使用同一场景版本与预算。"}), 409
    existing = final_run(version)
    if existing and existing.status != "failed":
        return jsonify({"error": "该作品已有最终评测记录。"}), 409
    packages = [a for a in version.assets if a.original_name.lower().endswith(".zip")]
    if len(packages) != 1:
        return jsonify({"error": "exactly one .zip evaluation package is required"}), 409
    package = packages[0]
    path = Path(current_app.config["UPLOAD_FOLDER"]) / package.storage_name
    if not path.is_file():
        return jsonify({"error": "submission package is unavailable"}), 409
    package_hash = package_digest(path)
    assets = []
    for asset in sorted(version.assets, key=lambda item: item.id):
        asset_path = Path(current_app.config["UPLOAD_FOLDER"]) / asset.storage_name
        if not asset_path.is_file():
            return jsonify({"error": "submission material is unavailable"}), 409
        assets.append({"id": asset.id, "name": asset.original_name, "sha256": package_digest(asset_path)})
    version_hash = digest({"snapshot": {k: v for k, v in version.snapshot.items()
        if k not in {"final_frozen_at", "final_evaluation_run_id"}}, "assets": assets})
    development_run = db.session.get(EvaluationRun, version.snapshot.get("evaluation_run_id"))
    if not development_run or development_run.submission_snapshot.get("package_sha256") != package_hash:
        return jsonify({"error": "作品包与关联开发记录不一致。"}), 409
    if existing and existing.submission_snapshot.get("package_sha256") != package_hash:
        return jsonify({"error": "冻结作品包已变化。"}), 409
    if existing and existing.submission_snapshot.get("version_sha256") != version_hash:
        return jsonify({"error": "冻结作品说明或附件已变化。"}), 409
    adapter = adapters()[config["adapter"]]
    stamp = now_utc().isoformat()
    frozen_at = version.snapshot.get("final_frozen_at", stamp)
    run = EvaluationRun(problem=problem, submission_version=version, purpose="final", status="queued",
        runtime_snapshot=deepcopy(runtime), config_snapshot={**deepcopy(config),
            "metric_definitions": [m for m in adapter["metrics"] if m["key"] in config["metrics"]]},
        submission_snapshot={"captured_at": version.snapshot["captured_at"], "asset_id": package.id,
            "asset_name": package.original_name, "package_sha256": package_hash,
            "frozen_at": frozen_at, "scene_set_version": label, "scene_set_sha256": set_hash,
            "final_config_sha256": config_hash, "asset_manifest": assets,
            "final_api_policy": final_api,
            "version_sha256": version_hash,
            **({"episode_weights": list(LIBERO_FINAL_WEIGHTS)} if libero_v2 else {}),
            "performance_scoring": deepcopy((problem.scoring_config or {}).get("performance_scoring"))})
    db.session.add(run)
    db.session.flush()
    version.snapshot = {**version.snapshot, "final_frozen_at": frozen_at, "final_evaluation_run_id": run.id}
    audit("evaluation.final_started", "evaluation_run", run.id, {"scene_set_version": label,
        "scene_set_sha256": set_hash, "package_sha256": package_hash})
    db.session.commit()
    return jsonify(run.to_dict()), 201


@evaluations_bp.post("/evaluation-runs/<run_id>/publish")
@require_user("admin", "organizer")
def publish_final(run_id):
    run = db.session.get(EvaluationRun, run_id)
    if not run or run.purpose != "final" or run.status != "completed" or final_run(run.submission_version) != run:
        return jsonify({"error": "当前作品的最终评测尚未完成。"}), 409
    run.submission_snapshot = {**run.submission_snapshot, "results_published_at": now_utc().isoformat()}
    audit("evaluation.final_published", "evaluation_run", run.id)
    db.session.commit()
    return jsonify(run.to_dict())


@evaluations_bp.post("/evaluation-worker/claim")
def claim_run():
    if not worker_authorized():
        return jsonify({"error": "worker authentication required"}), 401
    capabilities = request.get_json(silent=True)
    if (not isinstance(capabilities, dict) or not {"adapters", "gpu"} <= set(capabilities)
            or set(capabilities) - {"adapters", "gpu", "managed_runtime", "api_proxy", "worker_id", "legacy_adapters", "execution_backends", "runtime_images", "dataset_manifests", "memory_mb"}
            or not isinstance(capabilities["adapters"], list) or not capabilities["adapters"]
            or len(capabilities["adapters"]) > 64
            or any(not isinstance(item, str) or len(item) > 64 for item in capabilities["adapters"])
            or type(capabilities["gpu"]) is not bool
            or not isinstance(capabilities.get("legacy_adapters", []), list)
            or any(not isinstance(item, str) or item not in capabilities["adapters"] for item in capabilities.get("legacy_adapters", []))
            or any(type(capabilities.get(key, False)) is not bool for key in ("managed_runtime", "api_proxy"))
            or not isinstance(capabilities.get("worker_id", "legacy"), str)
            or not 1 <= len(capabilities.get("worker_id", "legacy")) <= 80):
        return jsonify({"error": "worker capabilities are required"}), 400
    backends = capabilities.get("execution_backends", ["docker"])
    runtime_images = capabilities.get("runtime_images")
    datasets = capabilities.get("dataset_manifests", {})
    memory_budget = capabilities.get("memory_mb")
    if (not isinstance(backends, list) or not backends or any(item not in ("docker", "autodl-native") for item in backends)
            or memory_budget is not None and (type(memory_budget) is not int or not 512 <= memory_budget <= 1048576)
            or runtime_images is not None and (not isinstance(runtime_images, list) or len(runtime_images) > 128
                or any(not isinstance(item, str) or not 1 <= len(item) <= 320 for item in runtime_images))
            or not isinstance(datasets, dict) or len(datasets) > 128
            or any(not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{2,80}", key)
                or not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value) for key, value in datasets.items())):
        return jsonify({"error": "invalid worker runtime capabilities"}), 400
    now = now_utc()
    state = db.session.get(EvaluationWorkerState, capabilities.get("worker_id", "legacy")) or EvaluationWorkerState(id=capabilities.get("worker_id", "legacy"))
    state.capabilities = deepcopy(capabilities)
    state.seen_at = now
    db.session.add(state)
    try:
        db.session.commit()
    except IntegrityError:
        # Two processes can advertise a new worker ID at the same instant.
        db.session.rollback()
        state = db.session.get(EvaluationWorkerState, capabilities.get("worker_id", "legacy"))
        if state is None:
            raise
        state.capabilities, state.seen_at = deepcopy(capabilities), now
        db.session.commit()
    from ..evaluation_queue import lock_worker, occupied
    worker_id = state.id
    query = EvaluationRun.query.filter(or_(
        EvaluationRun.status == "queued",
        and_(EvaluationRun.status == "running", EvaluationRun.lease_expires_at < now),
    ), EvaluationRun.config_snapshot["adapter"].as_string().in_(capabilities["adapters"]))
    execution = EvaluationRun.runtime_snapshot["execution"].as_string()
    query = query.filter(or_(execution.in_(backends), execution.is_(None) if "docker" in backends else False))
    if runtime_images is not None:
        query = query.filter(EvaluationRun.runtime_snapshot["image"].as_string().in_(runtime_images))
    if not capabilities["gpu"]:
        query = query.filter(EvaluationRun.config_snapshot["resources"]["gpu"].as_boolean() == False)
    if not capabilities.get("managed_runtime", False):
        query = query.filter(EvaluationRun.runtime_snapshot["image"].as_string().is_(None))
    elif "legacy_adapters" in capabilities:
        query = query.filter(or_(EvaluationRun.runtime_snapshot["image"].as_string().is_not(None),
            EvaluationRun.config_snapshot["adapter"].as_string().in_(capabilities["legacy_adapters"])))
    if not capabilities.get("api_proxy", True):
        query = query.filter(EvaluationRun.config_snapshot["api"]["enabled"].as_boolean() == False)
    if memory_budget is not None:
        from evaluation_resources import CONTROLLER_MEMORY_MB
        adapter = EvaluationRun.config_snapshot["adapter"].as_string()
        controller_cost = case(
            (and_(adapter == 'minecraft-agent-v1', EvaluationRun.config_snapshot['task'].as_string() == 'open-world'),
             CONTROLLER_MEMORY_MB['minecraft-agent-v1']),
            (adapter.in_(['libero-agent-v1', 'robot-arm-agent-v1']), CONTROLLER_MEMORY_MB['libero-agent-v1']),
            else_=0,
        )
        query = query.filter(EvaluationRun.config_snapshot['resources']['memory_mb'].as_integer() + controller_cost <= memory_budget)
    candidates = query.order_by(EvaluationRun.created_at.asc(), EvaluationRun.id.asc()).limit(25).all()
    for run in candidates:
        state = lock_worker(worker_id)
        active = occupied(worker_id, now)
        if active:
            state.capabilities = {**capabilities, "active_run_id": active.id}
            db.session.commit()
            return "", 204
        if run.judge_pool_id:
            from ..judge import lock_pool, worker_ready
            pool = lock_pool(run.judge_pool_id)
            if (pool.worker_id != capabilities.get("worker_id") or pool.state != "ready" or not worker_ready(pool)):
                db.session.rollback()
                continue
            busy = EvaluationRun.query.filter(EvaluationRun.judge_pool_id == pool.id,
                EvaluationRun.status == "running", EvaluationRun.lease_expires_at > now).first()
            if busy:
                db.session.rollback()
                continue
        if (run.runtime_snapshot or {}).get("execution") == "autodl-native" and any(
            datasets.get(scene["dataset"]) != scene["manifest_sha256"] for scene in run.runtime_snapshot["scenarios"]
        ):
            db.session.rollback()
            continue
        claimable = or_(EvaluationRun.status == "queued", and_(EvaluationRun.status == "running", EvaluationRun.lease_expires_at < now))
        if run.attempts >= MAX_ATTEMPTS:
            changed = db.session.execute(update(EvaluationRun).where(EvaluationRun.id == run.id, claimable).values(
                status="failed", error="worker lease expired", lease_hash=None, api_token_hash=None, finished_at=now,
            ).execution_options(synchronize_session=False))
            db.session.commit()
            if changed.rowcount:
                continue
        token = secrets.token_urlsafe(32)
        api_token = secrets.token_urlsafe(32)
        changed = db.session.execute(update(EvaluationRun).where(EvaluationRun.id == run.id, claimable).values(
            status="running", attempts=EvaluationRun.attempts + 1,
            worker_id=worker_id,
            lease_hash=hash_token(token), api_token_hash=hash_token(api_token),
            lease_expires_at=now + timedelta(seconds=LEASE_SECONDS), started_at=now,
        ).execution_options(synchronize_session=False))
        if not changed.rowcount:
            db.session.rollback()
            continue
        state.capabilities = {**capabilities, "active_run_id": run.id}
        db.session.commit()
        db.session.refresh(run)
        return jsonify({
            "id": run.id, "lease_token": token, "api_token": api_token, "lease_seconds": LEASE_SECONDS,
            "config": deepcopy(run.config_snapshot),
            "runtime": deepcopy(run.runtime_snapshot),
            "submission": deepcopy(run.submission_snapshot),
            "asset_url": f"/api/evaluation-worker/runs/{run.id}/asset",
            "logs_url": f"/api/evaluation-worker/runs/{run.id}/logs",
            "attempt": run.attempts,
        })
    return "", 204


@evaluations_bp.get("/evaluation-worker/runtime-catalog")
def worker_runtime_catalog():
    if not worker_authorized():
        return jsonify({"error": "worker authentication required"}), 401
    configured = {item.problem.evaluation_config.get("adapter") for item in ProblemRuntime.query.all()}
    # Keep frozen pending jobs discoverable after a problem is archived.
    configured.update(run.config_snapshot["adapter"] for run in EvaluationRun.query.filter(EvaluationRun.status.in_(["queued", "running"])).all() if run.runtime_snapshot)
    response = jsonify({"adapters": sorted(item for item in configured if item)})
    response.headers["Cache-Control"] = "no-store"
    return response


@evaluations_bp.post("/evaluation-worker/runs/<run_id>/heartbeat")
def heartbeat(run_id: str):
    run = leased_run(run_id)
    if not run:
        return jsonify({"error": "evaluation lease invalid or expired"}), 403
    run.lease_expires_at = now_utc() + timedelta(seconds=LEASE_SECONDS)
    db.session.execute(update(EvaluationWorkerState).where(
        or_(EvaluationWorkerState.id == run.worker_id,
            EvaluationWorkerState.capabilities["active_run_id"].as_string() == run.id)
    ).values(seen_at=now_utc()))
    db.session.commit()
    return jsonify({"lease_expires_at": iso(run.lease_expires_at)})


@evaluations_bp.get("/evaluation-worker/runs/<run_id>/asset")
def run_asset(run_id: str):
    run = leased_run(run_id)
    if not run:
        return jsonify({"error": "evaluation lease invalid or expired"}), 403
    if run.purpose == "trial":
        return send_from_directory(current_app.config["UPLOAD_FOLDER"], run.package_storage_name,
            as_attachment=True, download_name=run.submission_snapshot["asset_name"])
    asset = db.session.get(SubmissionAsset, run.submission_snapshot["asset_id"])
    if not asset or asset.submission_version_id != run.submission_version_id:
        return jsonify({"error": "evaluation asset unavailable"}), 404
    if run.purpose == "final" and package_digest(Path(current_app.config["UPLOAD_FOLDER"]) / asset.storage_name) != run.submission_snapshot["package_sha256"]:
        return jsonify({"error": "frozen submission package has changed"}), 409
    return send_from_directory(Path(current_app.config["UPLOAD_FOLDER"]), asset.storage_name, as_attachment=True, download_name=asset.original_name)


@evaluations_bp.post("/evaluation-worker/runs/<run_id>/api")
def proxy_api(run_id: str):
    run = db.session.get(EvaluationRun, run_id)
    token = request.headers.get("X-Evaluation-API-Token", "")
    if not run or not token or not run.api_token_hash or not hmac.compare_digest(run.api_token_hash, hash_token(token)) or run.status != "running" or not run.lease_expires_at or aware(run.lease_expires_at) <= now_utc():
        return jsonify({"error": "evaluation API token invalid or expired"}), 403
    policy = run.config_snapshot["api"]
    # Reuse team quota and usage records for every adapter, including Minecraft.
    from ..ai_models import AIGrant
    from ..ai_gateway import GatewayError, error_response, proxy
    team_id = run.submission_version.submission.team_id
    grant = AIGrant.query.filter_by(team_id=team_id, problem_id=run.problem_id).first()
    if grant is None:
        grant = AIGrant.query.filter_by(team_id=team_id, problem_id=None).first()
    if grant and run.purpose != "final":
        if not policy["enabled"]:
            return jsonify({"error": "evaluation API is unavailable"}), 503
        try:
            return proxy(grant, request.get_json(silent=True), run=run)
        except GatewayError as error:
            if error.code == "evaluation_limit":
                db.session.refresh(run)
                if run.api_calls_used >= policy["max_calls"]:
                    run.submission_snapshot = {**run.submission_snapshot, "api_call_budget_exhausted": True}
                    db.session.commit()
            return error_response(error)
    if (run.purpose != "final" and (run.submission_snapshot.get("performance_scoring") or {}).get("preset") == "wujie-libero-v2"):
        return jsonify({"error": "请在 API 平台开通本队 C 题额度。"}), 403
    upstream = current_app.config["EVALUATION_API_URL"]
    key = current_app.config["EVALUATION_API_KEY"]
    if not policy["enabled"] or not upstream or not key or not upstream.startswith("https://"):
        return jsonify({"error": "evaluation API is unavailable"}), 503
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "evaluation API expects a JSON object"}), 400
    final_api = run.submission_snapshot.get("final_api_policy") if run.purpose == "final" else None
    if run.purpose == "final":
        if not final_api:
            return jsonify({"error": "final API policy is unavailable"}), 503
        try:
            payload = validate_api_body(payload, final_api)
        except GatewayError as error:
            return error_response(error)
    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request_limit = final_api["max_request_bytes"] if final_api else 65536
    response_limit = final_api["max_response_bytes"] if final_api else 1024 * 1024
    timeout = final_api["timeout_seconds"] if final_api else 30
    if len(encoded) > request_limit:
        return jsonify({"error": "evaluation API request is too large"}), 413
    updated = db.session.execute(update(EvaluationRun).where(
        EvaluationRun.id == run.id, EvaluationRun.status == "running",
        EvaluationRun.lease_hash == run.lease_hash,
        EvaluationRun.api_calls_used < policy["max_calls"],
    ).values(api_calls_used=EvaluationRun.api_calls_used + 1))
    db.session.commit()
    if not updated.rowcount:
        run.submission_snapshot = {**run.submission_snapshot, "api_call_budget_exhausted": True}
        db.session.commit()
        return jsonify({"error": "evaluation API call limit reached"}), 429
    upstream_request = urllib.request.Request(upstream, data=encoded, headers={
        "Authorization": "Bearer " + key, "Content-Type": "application/json",
    }, method="POST")
    started = time.monotonic()
    call_record = {"call_index": run.api_calls_used, "model": payload.get("model"),
                   "request_bytes": len(encoded), "response_bytes": 0,
                   "upstream_status": None, "outcome": "upstream_unavailable"}
    try:
        with urllib.request.build_opener(NoRedirect).open(upstream_request, timeout=timeout) as response:
            call_record["upstream_status"] = getattr(response, "status", 200)
            result = response.read(response_limit + 1)
            call_record["response_bytes"] = len(result)
            if len(result) > response_limit:
                call_record["outcome"] = "response_too_large"
                return jsonify({"error": "evaluation API response is too large"}), 502
            call_record["outcome"] = "completed"
            return current_app.response_class(result, status=200, content_type="application/json")
    except urllib.error.HTTPError as error:
        call_record.update(upstream_status=error.code, outcome="upstream_http_error")
        error.close()
        return jsonify({"error": "evaluation API upstream unavailable"}), 502
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        call_record["outcome"] = "upstream_timeout" if isinstance(error, TimeoutError) or isinstance(getattr(error, "reason", None), TimeoutError) else "upstream_unavailable"
        return jsonify({"error": "evaluation API upstream unavailable"}), 502
    finally:
        if run.purpose == "final":
            call_record["elapsed_ms"] = round((time.monotonic() - started) * 1000, 3)
            audit("evaluation.final_api_call", "evaluation_run", run.id, call_record)
            db.session.commit()


def validate_partial_results(run, partial):
    """Completed LIBERO scenes are diagnostics, never a shortened scored run."""
    if run.config_snapshot["adapter"] != "libero-agent-v1":
        raise ValueError("partial results are only supported for LIBERO")
    if not isinstance(partial, dict) or set(partial) != {"episodes", "planned_episodes", "interrupted_scene", "reason"}:
        raise ValueError("invalid partial results")
    planned = run.config_snapshot["resources"]["episodes"]
    scenes = (run.runtime_snapshot or {}).get("scenarios", [])
    episodes = partial["episodes"]
    if (type(partial["planned_episodes"]) is not int or partial["planned_episodes"] != planned
            or len(scenes) != planned or not isinstance(episodes, list) or len(episodes) > planned):
        raise ValueError("partial episode count does not match frozen config")
    if partial["reason"] not in ("evaluation_time_limit", "execution_error"):
        raise ValueError("invalid interruption reason")
    stored = []
    for index, item in enumerate(episodes):
        expected = {key: scenes[index][key] for key in ("id", "label", "difficulty")}
        if not isinstance(item, dict) or set(item) != {"scenario", "metrics"} or item["scenario"] != expected:
            raise ValueError("partial result scenario does not match frozen scene manifest")
        stored.append({"scenario": expected, "metrics": validate_metrics(run.config_snapshot, item["metrics"])})
    interrupted = partial["interrupted_scene"]
    if interrupted is not None:
        if len(stored) >= planned or interrupted != {key: scenes[len(stored)][key] for key in ("id", "label", "difficulty")}:
            raise ValueError("interrupted scenario does not match frozen scene manifest")
    return {"episodes": stored, "planned_episodes": planned, "interrupted_scene": interrupted, "reason": partial["reason"]}


@evaluations_bp.post("/evaluation-worker/runs/<run_id>/complete")
def complete_run(run_id: str):
    run = leased_run(run_id)
    if not run:
        return jsonify({"error": "evaluation lease invalid or expired"}), 403
    version = run.submission_version
    if run.purpose != "trial" and (version.status != "submitted" or version.snapshot.get("captured_at") != run.submission_snapshot["captured_at"]):
        run.status = "superseded"
        run.lease_hash = None
        db.session.commit()
        return jsonify({"error": "submission has changed"}), 409
    data = request.get_json(silent=True) or {}
    partial = None
    if "partial_results" in data:
        if data.get("status") != "failed" or data.get("failure_kind") == "infrastructure_before_execution":
            return jsonify({"error": "partial results require an interrupted execution"}), 400
        try:
            partial = validate_partial_results(run, data["partial_results"])
        except ValueError as error:
            return jsonify({"error": str(error)}), 400
    if (data.get("status") == "failed" and partial is not None and data.get("budget_episodes") is not None
            and (run.submission_snapshot.get("performance_scoring") or {}).get("preset") == "wujie-libero-v2"
            and (partial["reason"] == "evaluation_time_limit" or run.submission_snapshot.get("api_call_budget_exhausted"))):
        reason = "evaluation_time_limit" if partial["reason"] == "evaluation_time_limit" else "api_call_limit"
        run.submission_snapshot = {**run.submission_snapshot, "budget_termination_reason": reason}
        data = {"status": "completed", "episodes": data["budget_episodes"]}
    if (data.get("status") == "failed" and data.get("failure_kind") == "infrastructure_before_execution"
            and run.judge_pool_id and run.purpose == "trial" and not run.quota_refunded):
        # Only the authenticated trusted controller receives the lease. This
        # marker is generated in its package download stage, never by a model.
        from ..judge import lock_pool
        lock_pool(run.judge_pool_id)
        changed = db.session.execute(update(EvaluationRun).where(EvaluationRun.id == run.id,
            EvaluationRun.status == "running", EvaluationRun.lease_hash == run.lease_hash,
            EvaluationRun.quota_refunded == False).values(quota_refunded=True).execution_options(synchronize_session=False))
        if changed.rowcount:
            db.session.execute(update(Submission).where(Submission.id == run.submission_version.submission_id,
                Submission.evaluation_runs_used > 0).values(evaluation_runs_used=Submission.evaluation_runs_used - 1))
        data = {**data, "error": "测评程序包传输失败，推理尚未执行，本次测试机会已退回。"}
    if data.get("status") == "failed":
        run.status = "failed"
        run.error = str(data.get("error", "evaluation failed"))[:500]
        run.metrics = {}
        run.episodes = partial["episodes"] if partial else []
        if partial:
            run.submission_snapshot = {**run.submission_snapshot, "partial_results": partial}
    elif data.get("status") == "completed":
        episodes = data.get("episodes")
        if not isinstance(episodes, list) or len(episodes) != run.config_snapshot["resources"]["episodes"]:
            return jsonify({"error": "episode count does not match evaluation config"}), 400
        try:
            readings = []
            stored_episodes = []
            for index, item in enumerate(episodes):
                if run.purpose == "final":
                    expected = {key: run.runtime_snapshot["scenarios"][index][key]
                                for key in ("id", "label", "difficulty")}
                    if not isinstance(item, dict) or item.get("scenario") != expected:
                        raise ValueError("final result scenario does not match frozen scene manifest")
                if isinstance(item, dict) and set(item) in (
                        {"scenario", "metrics"}, {"scenario", "metrics", "objectives"}):
                    scenario = item["scenario"]
                    if (not isinstance(scenario, dict) or set(scenario) != {"id", "label", "difficulty"}
                            or not isinstance(scenario["id"], str) or not 1 <= len(scenario["id"]) <= 64
                            or not isinstance(scenario["label"], str) or not 1 <= len(scenario["label"]) <= 80
                            or scenario["difficulty"] not in {"beginner", "intermediate", "challenge"}):
                        raise ValueError("invalid evaluation scenario metadata")
                    metrics = validate_metrics(run.config_snapshot, item["metrics"])
                    stored = {"scenario": scenario, "metrics": metrics}
                    if "objectives" in item:
                        objectives = item["objectives"]
                        if (not isinstance(objectives, list) or not 1 <= len(objectives) <= 20
                                or any(not isinstance(objective, dict)
                                       or set(objective) != {"id", "label", "completed"}
                                       or not isinstance(objective["id"], str)
                                       or not 1 <= len(objective["id"]) <= 160
                                       or not isinstance(objective["label"], str)
                                       or not 1 <= len(objective["label"]) <= 80
                                       or type(objective["completed"]) is not bool
                                       for objective in objectives)
                                or len({objective["id"] for objective in objectives}) != len(objectives)):
                            raise ValueError("invalid evaluation objective metadata")
                        stored["objectives"] = objectives
                    stored_episodes.append(stored)
                else:
                    metrics = validate_metrics(run.config_snapshot, item)
                    stored_episodes.append({
                        "scenario": {
                            "id": f"episode-{index + 1}",
                            "label": f"运行 {index + 1}",
                            "difficulty": "challenge",
                        },
                        "metrics": metrics,
                    })
                readings.append(metrics)
            from ..performance_scoring import performance_result
            performance_result(run.submission_snapshot.get("performance_scoring"),
                               run.config_snapshot["adapter"], stored_episodes,
                               run.submission_snapshot.get("episode_weights"))
        except ValueError as error:
            return jsonify({"error": str(error)}), 400
        run.episodes = stored_episodes
        run.metrics = {key: round(sum(item[key] for item in readings) / len(readings), 6) for key in run.config_snapshot["metrics"]}
        run.status = "completed"
    else:
        return jsonify({"error": "invalid evaluation result status"}), 400
    run.finished_at = now_utc()
    run.lease_hash = None
    run.api_token_hash = None
    run.lease_expires_at = None
    audit("evaluation.completed", "evaluation_run", run.id, {"status": run.status})
    db.session.commit()
    return jsonify(run.to_dict())
