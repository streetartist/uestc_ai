"""Team-owned, independently triggered evaluation attempts."""
from copy import deepcopy
from pathlib import Path
import shutil

from flask import Blueprint, current_app, jsonify, request

from ..ai_quotas import lock_problem
from ..evaluation import adapters, evaluation_budget
from ..evaluation_trials import package_digest
from ..extensions import db
from ..models import EvaluationRun, Problem, Registration, Submission, SubmissionVersion, Team, new_id, utcnow
from ..security import current_user, require_user, team_member
from ..utils import audit
from .submissions import current_version, deadline_passed, deadline_response, overwrite_version, retained_assets, staged_assets

evaluation_trials_bp = Blueprint("evaluation_trials", __name__)


@evaluation_trials_bp.get("/problems/<problem_id>/evaluation-runs")
@require_user()
def team_trials(problem_id):
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
    runs = EvaluationRun.query.join(SubmissionVersion).join(Submission).filter(
        EvaluationRun.problem_id == problem_id, Submission.team_id == team_id,
    ).order_by(EvaluationRun.created_at.desc()).all()
    response = jsonify([run.to_dict() for run in runs])
    response.headers["Cache-Control"] = "private, no-store"
    return response


@evaluation_trials_bp.post("/problems/<problem_id>/evaluation-runs")
@require_user()
def start_team_trial(problem_id):
    # Starting a trial leaves an existing formal submission and result intact.
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get("team_id"), str):
        return jsonify({"error": "team_id is required"}), 400
    user = current_user()
    if not team_member(data["team_id"], user.id):
        return jsonify({"error": "team membership required"}), 403
    problem = db.session.get(Problem, problem_id)
    team = db.session.get(Team, data["team_id"])
    if not problem or not team:
        return jsonify({"error": "problem or team not found"}), 404
    if problem.status != "published" or problem.track.competition.status != "published":
        return jsonify({"error": "problem is not open for submissions"}), 409
    if deadline_passed(problem.track.competition):
        return deadline_response()
    if team.competition_id != problem.track.competition_id:
        return jsonify({"error": "team belongs to a different competition"}), 400
    if not Registration.query.filter(Registration.team_id == team.id, Registration.status == "confirmed",
            (Registration.track_id == problem.track_id) | Registration.track_id.is_(None)).first():
        return jsonify({"error": "team is not registered for this track"}), 403
    lock_problem(problem.id)
    db.session.refresh(problem)
    config = problem.evaluation_config or {}
    adapter = adapters().get(config.get("adapter"))
    available = {item.strip() for item in current_app.config["EVALUATION_ENABLED_ADAPTERS"].split(",") if item.strip()}
    if not adapter or config.get("adapter") not in available or not current_app.config["EVALUATION_WORKER_TOKEN"]:
        return jsonify({"error": "evaluation adapter is not connected"}), 503
    from ..judge import configured_pool, available as judge_available
    pool = configured_pool(problem)
    if pool and not judge_available(pool, problem.runtime.config if problem.runtime else None):
        return jsonify({"error": "自动 GPU 测评暂不可用，请稍后重试，本次不扣次数。"}), 503
    if (problem.track.competition.config or {}).get("launch", {}).get("require_ready"):
        from ..problem_setup import readiness
        checks = readiness(problem)
        blocked = [item for item in checks["checks"] if item["state"] == "blocked"]
        if blocked:
            return jsonify({"error": "本题尚未完成开赛验收，本次不会扣除测试次数。", "checks": blocked}), 409
    if config["adapter"] in {"robot-arm-agent-v1", "libero-agent-v1"} and not problem.runtime:
        return jsonify({"error": "组织方尚未配置机械臂仿真环境与私有场景，请稍后测试。"}), 409
    if config["adapter"] == "classification-v1" and config["resources"]["gpu"] and not problem.runtime:
        return jsonify({"error": "组织方尚未安装 GPU 分类测评镜像与正式测试数据，请稍后测试。"}), 409
    submission = Submission.query.filter_by(problem_id=problem.id, team_id=team.id).populate_existing().first()
    budget = evaluation_budget(problem, submission)
    if budget["remaining_runs"] == 0:
        return jsonify({"error": "evaluation test limit reached", "evaluation_budget": budget}), 429
    if submission and EvaluationRun.query.join(SubmissionVersion).filter(SubmissionVersion.submission_id == submission.id,
            EvaluationRun.status.in_(["queued", "running"])).first():
        return jsonify({"error": "an evaluation trial is already active"}), 409
    staged, error = staged_assets(data, user.id)
    if error:
        return error
    retained, error = retained_assets(data, submission)
    if error:
        return error
    extension = adapter["submission"]["extension"]
    packages = [asset for asset in [*staged, *retained] if asset.original_name.lower().endswith("." + extension)]
    if len(packages) != 1:
        return jsonify({"error": f"exactly one .{extension} evaluation package is required"}), 400
    upload_folder = Path(current_app.config["UPLOAD_FOLDER"])
    source = upload_folder / packages[0].storage_name
    if not source.is_file():
        return jsonify({"error": "retained submission asset is unavailable"}), 409
    if not submission:
        submission = Submission(problem=problem, team=team, title=problem.title)
        db.session.add(submission)
        db.session.flush()
    version = current_version(submission)
    if not version:
        version, _ = overwrite_version(submission, {"title": submission.title, "status": "draft"}, user.id)
    run_id = new_id()
    storage_name = f"trial-{run_id}.{extension}"
    target = upload_folder / storage_name
    committed = False
    try:
        shutil.copyfile(source, target)
        run = EvaluationRun(id=run_id, problem=problem, submission_version=version, purpose="trial",
            package_storage_name=storage_name, status="queued",
            runtime_snapshot=deepcopy(problem.runtime.config) if problem.runtime else None,
            config_snapshot={**deepcopy(config), "metric_definitions": [metric for metric in adapter["metrics"] if metric["key"] in config["metrics"]]},
            submission_snapshot={"captured_at": utcnow().isoformat(), "asset_name": packages[0].original_name,
                "performance_scoring": deepcopy((problem.scoring_config or {}).get("performance_scoring")),
                "package_sha256": package_digest(target)})
        db.session.add(run)
        from ..judge import bind_run
        bind_run(run, problem)
        submission.evaluation_runs_used += 1
        audit("evaluation.trial_started", "evaluation_run", run.id, {"problem_id": problem.id, "team_id": team.id})
        db.session.commit()
        committed = True
        response = jsonify({**run.to_dict(), "evaluation_budget": evaluation_budget(problem, submission)})
        response.headers["Cache-Control"] = "private, no-store"
        return response, 201
    finally:
        if not committed:
            db.session.rollback()
            target.unlink(missing_ok=True)
