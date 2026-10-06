from datetime import datetime
from pathlib import Path
import shutil
from urllib.parse import urlsplit

from flask import Blueprint, current_app, jsonify, request, send_from_directory

from ..ai_quotas import lock_problem
from ..evaluation_trials import package_digest
from ..extensions import db
from ..models import EvaluationRun, Problem, Registration, StagedSubmissionAsset, Team, new_id, utcnow
from ..progress_models import CheckpointEntry
from ..progress import validate_checkpoints
from ..security import current_user, require_user, team_member
from ..utils import audit
from .submissions import deadline_passed

checkpoints_bp = Blueprint("checkpoints", __name__)


def visible(entry):
    user = current_user()
    return bool(user and (user.role in {"admin", "organizer"} or team_member(entry.team_id, user.id)))


@checkpoints_bp.get("/problems/<problem_id>/checkpoints")
@require_user()
def entries(problem_id):
    problem = db.session.get(Problem, problem_id)
    team = db.session.get(Team, request.args.get("team_id"))
    if not problem or not team:
        return jsonify({"error": "problem or team not found"}), 404
    if team.competition_id != problem.track.competition_id:
        return jsonify({"error": "team belongs to a different competition"}), 400
    if current_user().role not in {"admin", "organizer"} and not team_member(team.id, current_user().id):
        return jsonify({"error": "team membership required"}), 403
    result = jsonify({"definitions": (problem.track.competition.config or {}).get("checkpoints", []),
                      "entries": [entry.to_dict() for entry in CheckpointEntry.query.filter_by(problem_id=problem.id, team_id=team.id).all()]})
    result.headers["Cache-Control"] = "private, no-store"
    return result


@checkpoints_bp.post("/problems/<problem_id>/checkpoints/<checkpoint_id>")
@require_user()
def save_entry(problem_id, checkpoint_id):
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) - {"team_id", "content_md", "repository", "evaluation_run_id", "staged_asset_id"}:
        return jsonify({"error": "invalid checkpoint fields"}), 400
    team = db.session.get(Team, data.get("team_id")) if isinstance(data.get("team_id"), str) else None
    problem = db.session.get(Problem, problem_id)
    if not problem or not team:
        return jsonify({"error": "problem or team not found"}), 404
    if not team_member(team.id, current_user().id):
        return jsonify({"error": "team membership required"}), 403
    if team.competition_id != problem.track.competition_id:
        return jsonify({"error": "team belongs to a different competition"}), 400
    if problem.status != "published" or problem.track.competition.status != "published":
        return jsonify({"error": "problem is not open"}), 409
    if not Registration.query.filter(Registration.team_id == team.id, Registration.status == "confirmed",
                                    (Registration.track_id == problem.track_id) | Registration.track_id.is_(None)).first():
        return jsonify({"error": "team is not registered for this track"}), 403
    lock_problem(problem.id)
    db.session.refresh(problem.track.competition)
    definition = next((item for item in validate_checkpoints((problem.track.competition.config or {}).get("checkpoints", [])) if item["id"] == checkpoint_id), None)
    if not definition:
        return jsonify({"error": "checkpoint not configured"}), 404
    if deadline_passed(problem.track.competition) or definition["due_at"] and datetime.fromisoformat(definition["due_at"]) <= utcnow():
        return jsonify({"error": "checkpoint deadline has passed"}), 409
    content, repository = data.get("content_md", ""), data.get("repository", "")
    if not isinstance(content, str) or not 1 <= len(content.strip()) <= 20000 or not isinstance(repository, str) or len(repository) > 1000:
        return jsonify({"error": "请填写进展说明，最多20000字。"}), 400
    if repository:
        try:
            url = urlsplit(repository)
            valid_url = url.scheme == "https" and bool(url.hostname) and not url.username and not url.password
        except ValueError:
            valid_url = False
        if not valid_url:
            return jsonify({"error": "代码版本地址须为HTTPS链接。"}), 400
    run_id = data.get("evaluation_run_id") or None
    if run_id:
        run = db.session.get(EvaluationRun, run_id) if isinstance(run_id, str) else None
        if not run or run.problem_id != problem.id or run.submission_version.submission.team_id != team.id:
            return jsonify({"error": "请选择本队本题的测试记录。"}), 400
    entry = CheckpointEntry.query.filter_by(problem_id=problem.id, team_id=team.id, checkpoint_id=checkpoint_id).first()
    staged = None
    if data.get("staged_asset_id"):
        staged = db.session.get(StagedSubmissionAsset, data["staged_asset_id"]) if isinstance(data["staged_asset_id"], str) else None
        if not staged or staged.uploaded_by != current_user().id or not staged.original_name.lower().endswith(".zip"):
            return jsonify({"error": "请上传自己的代码ZIP。"}), 400
        source = Path(current_app.config["UPLOAD_FOLDER"]) / staged.storage_name
        if not source.is_file():
            return jsonify({"error": "代码附件已失效，请重新上传。"}), 400
    if not repository and not staged and not (entry and entry.package_storage_name):
        return jsonify({"error": "请上传代码ZIP或填写固定代码版本地址。"}), 400
    if entry:
        entry.revision += 1
    else:
        entry = CheckpointEntry(problem=problem, team=team, checkpoint_id=checkpoint_id)
        db.session.add(entry)
    folder, old_storage = Path(current_app.config["UPLOAD_FOLDER"]), None
    if staged:
        name = new_id() + ".zip"
        shutil.copyfile(folder / staged.storage_name, folder / name)
        old_storage = entry.package_storage_name
        entry.package_storage_name, entry.package_name = name, staged.original_name
        entry.package_sha256 = package_digest(folder / name)
    entry.content_md, entry.repository, entry.evaluation_run_id = content.strip(), repository, run_id
    entry.feedback_md, entry.updated_at = "", utcnow()
    db.session.flush()
    audit("checkpoint.saved", "checkpoint", entry.id, {"checkpoint": checkpoint_id, "revision": entry.revision})
    db.session.commit()
    if old_storage:
        (folder / old_storage).unlink(missing_ok=True)
    return jsonify(entry.to_dict()), 201


@checkpoints_bp.get("/checkpoints/<entry_id>/package")
@require_user()
def package(entry_id):
    entry = db.session.get(CheckpointEntry, entry_id)
    if not entry or not visible(entry):
        return jsonify({"error": "checkpoint not found"}), 404
    if not entry.package_storage_name:
        return jsonify({"error": "checkpoint has no package"}), 404
    response = send_from_directory(current_app.config["UPLOAD_FOLDER"], entry.package_storage_name, as_attachment=True, download_name=entry.package_name)
    response.headers["Cache-Control"] = "private, no-store"
    return response


@checkpoints_bp.get("/manage/checkpoints")
@require_user("admin", "organizer")
def managed_entries():
    query = CheckpointEntry.query
    if request.args.get("problem_id"):
        query = query.filter(CheckpointEntry.problem_id == request.args["problem_id"])
    return jsonify([entry.to_dict() for entry in query.order_by(CheckpointEntry.updated_at.desc()).limit(500).all()])


@checkpoints_bp.patch("/manage/checkpoints/<entry_id>")
@require_user("admin", "organizer")
def feedback(entry_id):
    entry = db.session.get(CheckpointEntry, entry_id)
    if not entry:
        return jsonify({"error": "checkpoint not found"}), 404
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) != {"feedback_md", "revision"} or not isinstance(data["feedback_md"], str) or len(data["feedback_md"]) > 20000:
        return jsonify({"error": "invalid feedback fields"}), 400
    lock_problem(entry.problem_id)
    db.session.refresh(entry)
    if type(data["revision"]) is not int or data["revision"] != entry.revision:
        return jsonify({"error": "队伍已更新进度，请刷新后重新反馈。"}), 409
    entry.feedback_md = data["feedback_md"]
    audit("checkpoint.feedback", "checkpoint", entry.id)
    db.session.commit()
    return jsonify(entry.to_dict())
