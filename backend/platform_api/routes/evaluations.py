from __future__ import annotations

import hmac
import json
import secrets
import urllib.error
import urllib.request
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request, send_from_directory
from sqlalchemy import and_, or_, update

from ..evaluation import catalog, evaluation_budget, validate_metrics
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
    if user.role not in {"admin", "organizer", "reviewer"} and not team_member(run.submission_version.submission.team_id, user.id):
        return jsonify({"error": "insufficient permissions"}), 403
    return jsonify(run.to_dict())


@evaluations_bp.get("/submission-versions/<version_id>/evaluation-runs")
@require_user()
def version_runs(version_id: str):
    version = db.session.get(SubmissionVersion, version_id)
    user = current_user()
    if not version:
        return jsonify({"error": "submission version not found"}), 404
    if user.role not in {"admin", "organizer", "reviewer"} and not team_member(version.submission.team_id, user.id):
        return jsonify({"error": "insufficient permissions"}), 403
    runs = EvaluationRun.query.filter_by(submission_version_id=version_id).order_by(EvaluationRun.created_at.desc()).all()
    return jsonify([run.to_dict() for run in runs])


@evaluations_bp.post("/evaluation-worker/claim")
def claim_run():
    if not worker_authorized():
        return jsonify({"error": "worker authentication required"}), 401
    capabilities = request.get_json(silent=True)
    if (not isinstance(capabilities, dict) or not {"adapters", "gpu"} <= set(capabilities)
            or set(capabilities) - {"adapters", "gpu", "managed_runtime", "api_proxy", "worker_id", "legacy_adapters"}
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
    now = now_utc()
    state = db.session.get(EvaluationWorkerState, capabilities.get("worker_id", "legacy")) or EvaluationWorkerState(id=capabilities.get("worker_id", "legacy"))
    state.capabilities = deepcopy(capabilities)
    state.seen_at = now
    db.session.add(state)
    db.session.commit()
    query = EvaluationRun.query.filter(or_(
        EvaluationRun.status == "queued",
        and_(EvaluationRun.status == "running", EvaluationRun.lease_expires_at < now),
    ), EvaluationRun.config_snapshot["adapter"].as_string().in_(capabilities["adapters"]))
    if not capabilities["gpu"]:
        query = query.filter(EvaluationRun.config_snapshot["resources"]["gpu"].as_boolean() == False)
    if not capabilities.get("managed_runtime", False):
        query = query.filter(EvaluationRun.runtime_snapshot["image"].as_string().is_(None))
    elif "legacy_adapters" in capabilities:
        query = query.filter(or_(EvaluationRun.runtime_snapshot["image"].as_string().is_not(None),
            EvaluationRun.config_snapshot["adapter"].as_string().in_(capabilities["legacy_adapters"])))
    if not capabilities.get("api_proxy", True):
        query = query.filter(EvaluationRun.config_snapshot["api"]["enabled"].as_boolean() == False)
    candidates = query.order_by(EvaluationRun.created_at.asc()).limit(25).all()
    for run in candidates:
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
            lease_hash=hash_token(token), api_token_hash=hash_token(api_token),
            lease_expires_at=now + timedelta(seconds=LEASE_SECONDS), started_at=now,
        ).execution_options(synchronize_session=False))
        db.session.commit()
        if not changed.rowcount:
            continue
        db.session.refresh(run)
        state.capabilities = {**capabilities, "active_run_id": run.id}
        db.session.commit()
        return jsonify({
            "id": run.id, "lease_token": token, "api_token": api_token, "lease_seconds": LEASE_SECONDS,
            "config": deepcopy(run.config_snapshot),
            "runtime": deepcopy(run.runtime_snapshot),
            "submission": deepcopy(run.submission_snapshot),
            "asset_url": f"/api/evaluation-worker/runs/{run.id}/asset",
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
        EvaluationWorkerState.capabilities["active_run_id"].as_string() == run.id
    ).values(seen_at=now_utc()))
    db.session.commit()
    return jsonify({"lease_expires_at": iso(run.lease_expires_at)})


@evaluations_bp.get("/evaluation-worker/runs/<run_id>/asset")
def run_asset(run_id: str):
    run = leased_run(run_id)
    if not run:
        return jsonify({"error": "evaluation lease invalid or expired"}), 403
    asset = db.session.get(SubmissionAsset, run.submission_snapshot["asset_id"])
    if not asset or asset.submission_version_id != run.submission_version_id:
        return jsonify({"error": "evaluation asset unavailable"}), 404
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
    if grant:
        if not policy["enabled"]:
            return jsonify({"error": "evaluation API is unavailable"}), 503
        try:
            return proxy(grant, request.get_json(silent=True), run=run)
        except GatewayError as error:
            return error_response(error)
    upstream = current_app.config["EVALUATION_API_URL"]
    key = current_app.config["EVALUATION_API_KEY"]
    if not policy["enabled"] or not upstream or not key or not upstream.startswith("https://"):
        return jsonify({"error": "evaluation API is unavailable"}), 503
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "evaluation API expects a JSON object"}), 400
    encoded = json.dumps(payload).encode("utf-8")
    if len(encoded) > 65536:
        return jsonify({"error": "evaluation API request is too large"}), 413
    updated = db.session.execute(update(EvaluationRun).where(
        EvaluationRun.id == run.id, EvaluationRun.status == "running",
        EvaluationRun.lease_hash == run.lease_hash,
        EvaluationRun.api_calls_used < policy["max_calls"],
    ).values(api_calls_used=EvaluationRun.api_calls_used + 1))
    db.session.commit()
    if not updated.rowcount:
        return jsonify({"error": "evaluation API call limit reached"}), 429
    upstream_request = urllib.request.Request(upstream, data=encoded, headers={
        "Authorization": "Bearer " + key, "Content-Type": "application/json",
    }, method="POST")
    try:
        with urllib.request.build_opener(NoRedirect).open(upstream_request, timeout=30) as response:
            result = response.read(1024 * 1024 + 1)
            if len(result) > 1024 * 1024:
                return jsonify({"error": "evaluation API response is too large"}), 502
            return current_app.response_class(result, status=200, content_type="application/json")
    except (urllib.error.URLError, TimeoutError, ValueError):
        return jsonify({"error": "evaluation API upstream unavailable"}), 502


@evaluations_bp.post("/evaluation-worker/runs/<run_id>/complete")
def complete_run(run_id: str):
    run = leased_run(run_id)
    if not run:
        return jsonify({"error": "evaluation lease invalid or expired"}), 403
    version = run.submission_version
    if version.status != "submitted" or version.snapshot.get("captured_at") != run.submission_snapshot["captured_at"]:
        run.status = "superseded"
        run.lease_hash = None
        db.session.commit()
        return jsonify({"error": "submission has changed"}), 409
    data = request.get_json(silent=True) or {}
    if data.get("status") == "failed":
        run.status = "failed"
        run.error = str(data.get("error", "evaluation failed"))[:500]
    elif data.get("status") == "completed":
        episodes = data.get("episodes")
        if not isinstance(episodes, list) or len(episodes) != run.config_snapshot["resources"]["episodes"]:
            return jsonify({"error": "episode count does not match evaluation config"}), 400
        try:
            readings = []
            stored_episodes = []
            for index, item in enumerate(episodes):
                if isinstance(item, dict) and set(item) == {"scenario", "metrics"}:
                    scenario = item["scenario"]
                    if (not isinstance(scenario, dict) or set(scenario) != {"id", "label", "difficulty"}
                            or not isinstance(scenario["id"], str) or not 1 <= len(scenario["id"]) <= 64
                            or not isinstance(scenario["label"], str) or not 1 <= len(scenario["label"]) <= 80
                            or scenario["difficulty"] not in {"beginner", "intermediate", "challenge"}):
                        raise ValueError("invalid evaluation scenario metadata")
                    metrics = validate_metrics(run.config_snapshot, item["metrics"])
                    stored_episodes.append({"scenario": scenario, "metrics": metrics})
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
