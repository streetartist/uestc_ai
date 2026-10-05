from __future__ import annotations
from copy import deepcopy

import csv
import io
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request, send_from_directory
from sqlalchemy import func
from ..extensions import db
from ..models import Competition, EvaluationRun, Problem, Registration, Review, Score, ScoreBatch, StagedSubmissionAsset, Submission, SubmissionAsset, SubmissionVersion, Team, Track, new_id
from ..evaluation import adapters, evaluation_budget
from ..ai_quotas import lock_problem
from ..submission_schema import validate_submission_materials
from ..reviewing import calculate_review_total, combined_score, effective_reviewer_weights, external_weight_percent, is_review_locked, latest_external_score, review_score
from ..security import current_user, require_user, team_member
from ..uploading import file_extension, original_filename, save_with_limit
from ..utils import audit, payload


submissions_bp = Blueprint("submissions", __name__)
ALLOWED_EXTENSIONS = {"md", "pdf", "csv", "json", "zip", "tar", "gz", "png", "jpg", "jpeg", "webp", "mp4"}
PUBLIC_VERSION_STATUSES = {"submitted", "confirmed", "published"}


def can_manage(submission: Submission, user_id: str) -> bool:
    return team_member(submission.team_id, user_id)


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


def deadline_passed(competition: Competition) -> bool:
    if not current_app.config.get("ENFORCE_COMPETITION_DEADLINES", True) or not competition.ends_at:
        return False
    return aware(competition.ends_at) <= datetime.now(timezone.utc)


def deadline_response():
    return jsonify({"error": "submission deadline has passed"}), 409


def current_version(submission: Submission) -> SubmissionVersion | None:
    return submission.versions[-1] if submission.versions else None


def public_work_payload(version: SubmissionVersion, include_content: bool = True) -> dict:
    submission = version.submission
    snapshot = version.snapshot or {}
    data = {
        **submission.to_dict(),
        "title": snapshot.get("title") or submission.title,
        "track_name": submission.problem.track.name,
        "track_slug": submission.problem.track.slug,
        "competition_name": submission.problem.track.competition.name,
        "competition_slug": submission.problem.track.competition.slug,
        "team_members": [{"id": member.user.id, "name": member.user.name} for member in submission.team.members],
        "problem": {
            "id": submission.problem.id,
            "code": submission.problem.code,
            "slug": submission.problem.slug,
            "title": submission.problem.title,
        },
        "track": {
            "id": submission.problem.track.id,
            "slug": submission.problem.track.slug,
            "name": submission.problem.track.name,
        },
        "competition": {
            "id": submission.problem.track.competition.id,
            "slug": submission.problem.track.competition.slug,
            "name": submission.problem.track.competition.name,
        },
        "version": version.to_dict() if include_content else {
            "id": version.id,
            "submission_id": version.submission_id,
            "version": version.version,
            "status": version.status,
            "created_at": version.created_at.isoformat(),
        },
    }
    if include_content:
        data["assets"] = [asset.to_dict() for asset in version.assets]
        data["evaluation_config"] = submission.problem.evaluation_config
        data["evaluation"] = current_evaluation(version)
    return data


def current_evaluation(version: SubmissionVersion):
    run = EvaluationRun.query.filter_by(submission_version_id=version.id).order_by(EvaluationRun.created_at.desc()).first()
    if not run or run.status == "superseded" or run.submission_snapshot.get("captured_at") != (version.snapshot or {}).get("captured_at"):
        return None
    return run.to_dict()


def overwrite_version(submission: Submission, data: dict, user_id: str):
    version = current_version(submission)
    removed_storage_names = []
    version_ids = [item.id for item in submission.versions]
    if version_ids:
        for run in EvaluationRun.query.filter(EvaluationRun.submission_version_id.in_(version_ids)).all():
            if run.status in {"queued", "running", "completed"}:
                run.status = "superseded"
                run.lease_hash = None
                run.api_token_hash = None
                run.lease_expires_at = None
    if version_ids:
        Review.query.filter(Review.submission_version_id.in_(version_ids)).delete(synchronize_session=False)
        Score.query.filter(Score.submission_version_id.in_(version_ids)).delete(synchronize_session=False)
    for obsolete in list(submission.versions[:-1]):
        removed_storage_names.extend(asset.storage_name for asset in obsolete.assets)
        db.session.delete(obsolete)
    if submission.versions[:-1]:
        db.session.flush()

    status = data.get("status", "draft")
    now = datetime.now(timezone.utc)
    snapshot = {
        "submission_id": submission.id,
        "version": 1,
        "problem": {"id": submission.problem.id, "code": submission.problem.code, "title": submission.problem.title},
        "team": {"id": submission.team.id, "name": submission.team.name, "members": [member.user.to_dict() for member in submission.team.members]},
        "title": data.get("title", submission.title),
        "readme_md": data.get("readme_md", ""),
        "fields": data.get("fields", {}),
        "captured_at": now.isoformat(),
    }
    if version:
        version.version = 1
        version.readme_md = snapshot["readme_md"]
        version.fields = snapshot["fields"]
        version.snapshot = snapshot
        version.status = status
        version.created_by = user_id
        version.created_at = now
    else:
        version = SubmissionVersion(
            submission=submission,
            version=1,
            readme_md=snapshot["readme_md"],
            fields=snapshot["fields"],
            snapshot=snapshot,
            status=status,
            created_by=user_id,
            created_at=now,
        )
        db.session.add(version)
    submission.current_version = 1
    submission.title = snapshot["title"]
    submission.status = status
    return version, removed_storage_names


def staged_assets(data: dict, user_id: str):
    asset_ids = data.get("staged_asset_ids", [])
    if not isinstance(asset_ids, list) or any(not isinstance(asset_id, str) for asset_id in asset_ids):
        return None, (jsonify({"error": "staged_asset_ids must be a list"}), 400)
    if len(asset_ids) != len(set(asset_ids)):
        return None, (jsonify({"error": "staged_asset_ids contains duplicates"}), 400)
    if not asset_ids:
        return [], None
    assets = StagedSubmissionAsset.query.filter(
        StagedSubmissionAsset.id.in_(asset_ids),
        StagedSubmissionAsset.uploaded_by == user_id,
    ).all()
    if len(assets) != len(asset_ids):
        return None, (jsonify({"error": "invalid staged submission assets"}), 400)
    by_id = {asset.id: asset for asset in assets}
    return [by_id[asset_id] for asset_id in asset_ids], None


def retained_assets(data: dict, submission: Submission | None):
    asset_ids = data.get("retained_asset_ids", [])
    if not isinstance(asset_ids, list) or any(not isinstance(asset_id, str) for asset_id in asset_ids):
        return None, (jsonify({"error": "retained_asset_ids must be a list"}), 400)
    if len(asset_ids) != len(set(asset_ids)):
        return None, (jsonify({"error": "retained_asset_ids contains duplicates"}), 400)
    if not asset_ids:
        return [], None
    latest_version = submission.versions[-1] if submission and submission.versions else None
    if not latest_version:
        return None, (jsonify({"error": "invalid retained submission assets"}), 400)
    assets = SubmissionAsset.query.filter(
        SubmissionAsset.id.in_(asset_ids),
        SubmissionAsset.submission_version_id == latest_version.id,
    ).all()
    if len(assets) != len(asset_ids):
        return None, (jsonify({"error": "invalid retained submission assets"}), 400)
    by_id = {asset.id: asset for asset in assets}
    return [by_id[asset_id] for asset_id in asset_ids], None


@submissions_bp.post("/submissions")
@require_user()
def submit_work():
    user = current_user()
    data, error = payload(("problem_id", "team_id", "title"))
    if error:
        return error
    data.setdefault("readme_md", "")
    status = data.get("status", "draft")
    if status not in {"draft", "submitted"}:
        return jsonify({"error": "invalid submission status"}), 400
    staged, staged_error = staged_assets(data, user.id)
    if staged_error:
        return staged_error
    if not team_member(data["team_id"], user.id):
        return jsonify({"error": "team membership required"}), 403
    problem = db.session.get(Problem, data["problem_id"])
    team = db.session.get(Team, data["team_id"])
    if not problem or not team:
        return jsonify({"error": "problem or team not found"}), 404
    if problem.status != "published" or problem.track.competition.status != "published":
        return jsonify({"error": "problem is not open for submissions"}), 409
    if deadline_passed(problem.track.competition):
        return deadline_response()
    if problem.track.competition_id != team.competition_id:
        return jsonify({"error": "team belongs to a different competition"}), 400
    registration = Registration.query.filter(
        Registration.competition_id == team.competition_id,
        Registration.team_id == team.id,
        Registration.status == "confirmed",
        (Registration.track_id == problem.track_id) | (Registration.track_id.is_(None)),
    ).first()
    if not registration:
        return jsonify({"error": "team is not registered for this track"}), 403
    # Serialize quota reads, first submissions and overwrites with policy saves.
    lock_problem(problem.id)
    db.session.refresh(problem)
    if status == "submitted" and problem.evaluation_config.get("adapter") == "robot-arm-agent-v1" and not problem.runtime:
        db.session.rollback()
        return jsonify({"error": "组织方尚未配置机械臂仿真环境与私有场景，请稍后提交。"}), 409
    submission = Submission.query.filter_by(problem_id=problem.id, team_id=team.id).populate_existing().first()
    budget = evaluation_budget(problem, submission)
    if status == "submitted" and budget["enabled"] and budget["remaining_runs"] == 0:
        db.session.rollback()
        return jsonify({"error": "evaluation test limit reached", "evaluation_budget": budget}), 429
    retained, retained_error = retained_assets(data, submission)
    if retained_error:
        return retained_error
    try:
        validate_submission_materials(problem.submission_schema or {}, data, [*staged, *retained], status == "submitted", Path(current_app.config["UPLOAD_FOLDER"]))
    except ValueError as validation_error:
        return jsonify({"error": str(validation_error)}), 400
    if not submission:
        submission = Submission(problem=problem, team=team, title=data["title"])
        db.session.add(submission)
        db.session.flush()
    version, removed_storage_names = overwrite_version(submission, data, user.id)
    visibility = "private" if status == "draft" else "public"
    upload_folder = Path(current_app.config["UPLOAD_FOLDER"])
    if any(not (upload_folder / asset.storage_name).is_file() for asset in retained):
        db.session.rollback()
        return jsonify({"error": "retained submission asset is unavailable"}), 409
    retained_ids = {asset.id for asset in retained}
    for asset in list(version.assets):
        if asset.id in retained_ids:
            asset.visibility = visibility
        else:
            removed_storage_names.append(asset.storage_name)
            db.session.delete(asset)
    for staged_asset in staged:
        db.session.add(SubmissionAsset(
            version=version,
            original_name=staged_asset.original_name,
            storage_name=staged_asset.storage_name,
            content_type=staged_asset.content_type,
            size=staged_asset.size,
            visibility=visibility,
        ))
        db.session.delete(staged_asset)
    evaluation_config = problem.evaluation_config or {}
    if status == "submitted" and evaluation_config:
        available = {item.strip() for item in current_app.config["EVALUATION_ENABLED_ADAPTERS"].split(",") if item.strip()}
        if evaluation_config["adapter"] not in available or not current_app.config["EVALUATION_WORKER_TOKEN"]:
            db.session.rollback()
            return jsonify({"error": "evaluation adapter is not connected"}), 503
        adapter = adapters().get(evaluation_config["adapter"])
        if not adapter:
            db.session.rollback()
            return jsonify({"error": "evaluation adapter is no longer registered"}), 409
        extension = adapter["submission"]["extension"]
        # Overwrite removes old assets; reload the relationship before checking
        # the new package so an uploaded replacement isn't counted twice.
        db.session.flush()
        db.session.expire(version, ["assets"])
        packages = [asset for asset in version.assets if asset.original_name.lower().endswith("." + extension)]
        if len(packages) != 1:
            db.session.rollback()
            return jsonify({"error": f"exactly one .{extension} evaluation package is required"}), 400
        db.session.flush()
        db.session.add(EvaluationRun(
            problem=problem, submission_version=version,
            runtime_snapshot=deepcopy(problem.runtime.config) if problem.runtime else None,
            config_snapshot={**evaluation_config, "metric_definitions": [metric for metric in adapter["metrics"] if metric["key"] in evaluation_config["metrics"]]},
            submission_snapshot={
                "captured_at": version.snapshot["captured_at"], "asset_id": packages[0].id,
                "asset_name": packages[0].original_name,
            },
            status="queued",
        ))
        submission.evaluation_runs_used += 1
    audit("submission.overwritten", "submission", submission.id, {
        "status": version.status,
        "asset_count": len(staged) + len(retained),
    })
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    for storage_name in removed_storage_names:
        (upload_folder / storage_name).unlink(missing_ok=True)
    version_data = version.to_dict()
    version_data["assets"] = [asset.to_dict() for asset in version.assets]
    return jsonify({**submission.to_dict(), "version": version_data}), 201


@submissions_bp.get("/submissions/<submission_id>")
@require_user()
def submission_detail(submission_id: str):
    submission = db.session.get(Submission, submission_id)
    if not submission:
        return jsonify({"error": "submission not found"}), 404
    user = current_user()
    if not can_manage(submission, user.id):
        return jsonify({"error": "submission not found"}), 404
    data = submission.to_dict(include_versions=True)
    for item, version in zip(data["versions"], submission.versions):
        item["assets"] = [asset.to_dict() for asset in version.assets]
    return jsonify(data)


@submissions_bp.get("/works")
def public_work_archive():
    query = (
        Submission.query
        .join(Problem)
        .join(Track)
        .join(Competition)
        .filter(Submission.status.in_(PUBLIC_VERSION_STATUSES))
    )
    if request.args.get("competition"):
        query = query.filter(Competition.slug == request.args["competition"])
    if request.args.get("track"):
        query = query.filter(Track.slug == request.args["track"])
    if request.args.get("problem"):
        query = query.filter(Problem.slug == request.args["problem"])
    submissions = query.order_by(Submission.updated_at.desc()).all()
    versions = [current_version(submission) for submission in submissions]
    return jsonify([public_work_payload(version, include_content=False) for version in versions if version and version.status in PUBLIC_VERSION_STATUSES])


@submissions_bp.get("/works/<submission_id>")
def public_work(submission_id: str):
    submission = db.session.get(Submission, submission_id)
    if not submission or submission.status not in PUBLIC_VERSION_STATUSES:
        return jsonify({"error": "work not published"}), 404
    version = current_version(submission)
    if not version or version.status not in PUBLIC_VERSION_STATUSES:
        return jsonify({"error": "work has no public version"}), 404
    return jsonify(public_work_payload(version))


@submissions_bp.get("/me/submissions")
@require_user()
def my_submissions():
    user = current_user()
    items = Submission.query.join(Team).join(Team.members).filter_by(user_id=user.id).order_by(Submission.updated_at.desc()).all()
    return jsonify([item.to_dict() for item in items])


@submissions_bp.delete("/submissions/<submission_id>")
@require_user()
def delete_draft_submission(submission_id: str):
    user = current_user()
    submission = db.session.get(Submission, submission_id)
    if not submission:
        return jsonify({"error": "submission not found"}), 404
    if not can_manage(submission, user.id):
        return jsonify({"error": "team membership required"}), 403
    if deadline_passed(submission.problem.track.competition):
        return deadline_response()
    lock_problem(submission.problem_id)
    submission = db.session.get(Submission, submission_id, populate_existing=True)
    if not submission:
        return jsonify({"error": "submission not found"}), 404
    version_ids = [version.id for version in submission.versions]
    has_review = Review.query.filter(Review.submission_version_id.in_(version_ids)).first() if version_ids else None
    has_score = Score.query.filter(Score.submission_version_id.in_(version_ids)).first() if version_ids else None
    has_run = EvaluationRun.query.filter(EvaluationRun.submission_version_id.in_(version_ids)).first() if version_ids else None
    if submission.status != "draft" or any(version.status != "draft" for version in submission.versions) or has_review or has_score or has_run or submission.evaluation_runs_used:
        return jsonify({"error": "only unreviewed drafts can be deleted"}), 409
    storage_names = [asset.storage_name for version in submission.versions for asset in version.assets]
    audit("submission.deleted", "submission", submission.id, {"title": submission.title})
    db.session.delete(submission)
    db.session.commit()
    upload_folder = Path(current_app.config["UPLOAD_FOLDER"])
    for storage_name in storage_names:
        (upload_folder / storage_name).unlink(missing_ok=True)
    return "", 204


@submissions_bp.post("/submission-assets/stage")
@require_user()
def stage_submission_asset():
    user = current_user()
    problem_id = request.form.get("problem_id")
    if problem_id:
        problem = db.session.get(Problem, problem_id)
        if not problem:
            return jsonify({"error": "problem not found"}), 404
        if deadline_passed(problem.track.competition):
            return deadline_response()
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "file is required"}), 400
    display_name = original_filename(file.filename)
    extension = file_extension(display_name)
    if not display_name or extension not in ALLOWED_EXTENSIONS:
        return jsonify({"error": "file type is not allowed"}), 400
    asset_id = new_id()
    storage_name = f"staged-{user.id}-{asset_id}.{extension}"
    target = Path(current_app.config["UPLOAD_FOLDER"]) / storage_name
    size_limit = int(current_app.config["SUBMISSION_ASSET_MAX_SIZE"])
    try:
        size = save_with_limit(file.stream, target, size_limit)
    except ValueError:
        return jsonify({"error": "submission asset exceeds size limit", "limit": size_limit}), 413
    asset = StagedSubmissionAsset(
        id=asset_id,
        uploaded_by=user.id,
        original_name=display_name,
        storage_name=storage_name,
        content_type=file.mimetype or "application/octet-stream",
        size=size,
    )
    db.session.add(asset)
    db.session.commit()
    return jsonify(asset.to_dict()), 201


@submissions_bp.delete("/submission-assets/stage/<asset_id>")
@require_user()
def delete_staged_submission_asset(asset_id: str):
    user = current_user()
    asset = db.session.get(StagedSubmissionAsset, asset_id)
    if not asset or asset.uploaded_by != user.id:
        return jsonify({"error": "staged submission asset not found"}), 404
    storage_name = asset.storage_name
    db.session.delete(asset)
    db.session.commit()
    (Path(current_app.config["UPLOAD_FOLDER"]) / storage_name).unlink(missing_ok=True)
    return "", 204


@submissions_bp.post("/submission-versions/<version_id>/assets")
@require_user()
def upload_asset(version_id: str):
    user = current_user()
    version = db.session.get(SubmissionVersion, version_id)
    if not version:
        return jsonify({"error": "submission version not found"}), 404
    if not can_manage(version.submission, user.id):
        return jsonify({"error": "team membership required"}), 403
    if deadline_passed(version.submission.problem.track.competition):
        return deadline_response()
    if version.status in PUBLIC_VERSION_STATUSES and version.submission.problem.evaluation_config:
        return jsonify({"error": "update the submission to change evaluation attachments"}), 409
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "file is required"}), 400
    display_name = original_filename(file.filename)
    extension = file_extension(display_name)
    if not display_name or extension not in ALLOWED_EXTENSIONS:
        return jsonify({"error": "file type is not allowed"}), 400
    asset_id = new_id()
    storage_name = f"{version.id}-{asset_id}.{extension}"
    target = Path(current_app.config["UPLOAD_FOLDER"]) / storage_name
    size_limit = int(current_app.config["SUBMISSION_ASSET_MAX_SIZE"])
    try:
        size = save_with_limit(file.stream, target, size_limit)
    except ValueError:
        return jsonify({"error": "submission asset exceeds size limit", "limit": size_limit}), 413
    visibility = "public" if version.status in PUBLIC_VERSION_STATUSES else "private"
    Review.query.filter_by(submission_version_id=version.id).delete(synchronize_session=False)
    Score.query.filter_by(submission_version_id=version.id).delete(synchronize_session=False)
    asset = SubmissionAsset(id=asset_id, version=version, original_name=display_name, storage_name=storage_name, content_type=file.mimetype or "application/octet-stream", size=size, visibility=visibility)
    db.session.add(asset)
    db.session.flush()
    audit("submission.asset_uploaded", "submission", version.submission_id, {"asset_id": asset.id, "name": display_name})
    db.session.commit()
    return jsonify(asset.to_dict()), 201


@submissions_bp.get("/assets/<asset_id>")
def download_asset(asset_id: str):
    asset = db.session.get(SubmissionAsset, asset_id)
    if not asset:
        return jsonify({"error": "asset not found"}), 404
    user = current_user()
    if asset.version.status not in PUBLIC_VERSION_STATUSES:
        allowed = user and (can_manage(asset.version.submission, user.id) or user.role in {"admin", "organizer"})
        if not allowed:
            return jsonify({"error": "asset not found"}), 404
    return send_from_directory(current_app.config["UPLOAD_FOLDER"], asset.storage_name, as_attachment=True, download_name=asset.original_name)


@submissions_bp.post("/reviews")
@require_user("admin", "organizer", "reviewer")
def save_review():
    user = current_user()
    data, error = payload(("submission_version_id", "scores"))
    if error:
        return error
    version = db.session.get(SubmissionVersion, data["submission_version_id"])
    if not version:
        return jsonify({"error": "submission version not found"}), 404
    if version.status not in PUBLIC_VERSION_STATUSES or version.submission.status not in PUBLIC_VERSION_STATUSES or current_version(version.submission) is not version:
        return jsonify({"error": "submission is not formally submitted"}), 409
    if external_weight_percent(version.submission.problem) >= 100:
        return jsonify({"error": "this problem uses external-only scoring"}), 409
    competition = version.submission.problem.track.competition
    if is_review_locked(competition):
        return jsonify({"error": "online review is locked for this competition"}), 409
    competition_id = competition.id
    configured, weights = effective_reviewer_weights(competition_id)
    if not configured or user.id not in weights:
        return jsonify({"error": "reviewer is not assigned to this competition"}), 403
    if version.submission.problem.evaluation_config:
        evaluation = current_evaluation(version)
        if not evaluation or evaluation["status"] != "completed":
            return jsonify({"error": "evaluation must complete before review"}), 409
    try:
        total_score = calculate_review_total(version.submission.problem, data["scores"], data.get("total_score"))
    except (TypeError, ValueError) as error:
        return jsonify({"error": str(error)}), 400
    review = Review.query.filter_by(submission_version_id=version.id, reviewer_id=user.id).first()
    if not review:
        review = Review(submission_version_id=version.id, reviewer_id=user.id)
        db.session.add(review)
    review.scores = data["scores"]
    review.total_score = total_score
    review.feedback_md = data.get("feedback_md", "")
    review.status = data.get("status", "submitted")
    db.session.flush()
    audit("review.saved", "review", review.id, {"submission_version_id": version.id})
    db.session.commit()
    return jsonify({"id": review.id, "status": review.status, "total_score": review.total_score}), 201


@submissions_bp.get("/review-queue")
@require_user("admin", "organizer", "reviewer")
def review_queue():
    user = current_user()
    query = (
        SubmissionVersion.query
        .join(Submission)
        .join(Problem)
        .join(Track)
        .filter(
            Submission.status.in_(PUBLIC_VERSION_STATUSES),
            SubmissionVersion.status.in_(PUBLIC_VERSION_STATUSES),
            SubmissionVersion.version == Submission.current_version,
        )
    )
    if request.args.get("competition_id"):
        query = query.filter(Track.competition_id == request.args["competition_id"])
    if request.args.get("track"):
        query = query.filter(Track.slug == request.args["track"])
    versions = query.order_by(SubmissionVersion.created_at.asc()).all()
    weight_cache = {}
    visible_versions = []
    for version in versions:
        if external_weight_percent(version.submission.problem) >= 100:
            continue
        competition_id = version.submission.problem.track.competition_id
        if competition_id not in weight_cache:
            configured, weights = effective_reviewer_weights(competition_id)
            weight_cache[competition_id] = (configured, weights)
        configured, weights = weight_cache[competition_id]
        if not configured or user.id not in weights:
            continue
        visible_versions.append(version)
    versions = visible_versions
    reviews = {
        item.submission_version_id: item
        for item in Review.query.filter(
            Review.reviewer_id == user.id,
            Review.submission_version_id.in_([version.id for version in versions]),
        ).all()
    } if versions else {}
    return jsonify([{
        **version.to_dict(),
        "submission": version.submission.to_dict(),
        "team": version.submission.team.to_dict(include_members=True),
        "problem": version.submission.problem.to_dict(),
        "track": version.submission.problem.track.to_dict(),
        "competition": version.submission.problem.track.competition.to_dict(),
        "review_locked": is_review_locked(version.submission.problem.track.competition),
        "assets": [asset.to_dict() for asset in version.assets],
        "evaluation": current_evaluation(version),
        "reviewer_weight_percent": (
            weight_cache[version.submission.problem.track.competition_id][1].get(user.id)
            * (100 - external_weight_percent(version.submission.problem)) / 100
            if user.id in weight_cache[version.submission.problem.track.competition_id][1]
            else None
        ),
        "my_review": ({
            "id": reviews[version.id].id,
            "scores": reviews[version.id].scores,
            "total_score": reviews[version.id].total_score,
            "feedback_md": reviews[version.id].feedback_md,
            "status": reviews[version.id].status,
        } if version.id in reviews else None),
    } for version in versions])


@submissions_bp.post("/scores/import")
@require_user("admin", "organizer")
def import_scores():
    user = current_user()
    data, error = payload(("competition_id", "source", "label", "records"))
    if error:
        return error
    return persist_score_batch(data, user.id)


def persist_score_batch(data: dict, user_id: str):
    if not isinstance(data["records"], list) or not data["records"]:
        return jsonify({"error": "records must be a non-empty list"}), 400
    competition = db.session.get(Competition, data["competition_id"])
    if not competition:
        return jsonify({"error": "competition not found"}), 404
    if not data.get("problem_id"):
        return jsonify({"error": "problem_id is required"}), 400
    problem = db.session.get(Problem, data["problem_id"])
    if not problem:
        return jsonify({"error": "problem not found"}), 404
    if problem and problem.track.competition_id != competition.id:
        return jsonify({"error": "problem belongs to a different competition"}), 400
    if problem and external_weight_percent(problem) <= 0:
        return jsonify({"error": "external scoring is not enabled for this problem"}), 409
    batch = ScoreBatch(
        competition_id=competition.id,
        problem_id=problem.id,
        source=data["source"],
        label=data["label"],
        imported_by=user_id,
        status=data.get("status", "confirmed"),
    )
    db.session.add(batch)
    db.session.flush()
    accepted = 0
    errors = []
    seen_versions = set()
    for index, record in enumerate(data["records"], 1):
        if not isinstance(record, dict):
            errors.append({"row": index, "error": "record must be an object"})
            continue
        version = db.session.get(SubmissionVersion, record.get("submission_version_id"))
        if not version or version.submission.problem.track.competition_id != competition.id:
            errors.append({"row": index, "error": "invalid submission_version_id"})
            continue
        if version.status not in PUBLIC_VERSION_STATUSES or version.submission.status not in PUBLIC_VERSION_STATUSES or current_version(version.submission) is not version:
            errors.append({"row": index, "error": "submission version is not formally submitted"})
            continue
        if problem and version.submission.problem_id != problem.id:
            errors.append({"row": index, "error": "submission version belongs to a different problem"})
            continue
        if batch.problem_id and version.submission.problem_id != batch.problem_id:
            errors.append({"row": index, "error": "score batch must contain one problem"})
            continue
        if not batch.problem_id:
            batch.problem_id = version.submission.problem_id
        if version.id in seen_versions:
            errors.append({"row": index, "error": "duplicate submission_version_id"})
            continue
        seen_versions.add(version.id)
        try:
            metrics = {str(key): float(value) for key, value in (record.get("metrics") or {}).items()}
            if record.get("total_score") is None:
                raise ValueError("total_score is required")
            total_score = float(record["total_score"])
            rank = int(record["rank"]) if record.get("rank") is not None else None
        except (AttributeError, TypeError, ValueError):
            errors.append({"row": index, "error": "score values must be numeric"})
            continue
        db.session.add(Score(batch=batch, submission_version_id=version.id, metrics=metrics, total_score=total_score, rank=rank, feedback_md=record.get("feedback_md", "")))
        accepted += 1
    if not accepted:
        db.session.rollback()
        return jsonify({"error": "no valid score records", "records": errors}), 400
    audit("score_batch.imported", "score_batch", batch.id, {"accepted": accepted, "errors": errors})
    db.session.commit()
    return jsonify({"batch_id": batch.id, "accepted": accepted, "errors": errors}), 201


@submissions_bp.post("/scores/import-csv")
@require_user("admin", "organizer")
def import_scores_csv():
    file = request.files.get("file")
    competition_id = request.form.get("competition_id")
    problem_id = request.form.get("problem_id")
    if not file or not competition_id or not problem_id:
        return jsonify({"error": "file, competition_id and problem_id are required"}), 400
    try:
        rows = list(csv.DictReader(io.StringIO(file.read().decode("utf-8-sig"))))
    except UnicodeDecodeError:
        return jsonify({"error": "CSV must use UTF-8 encoding"}), 400
    if not rows or any(field not in rows[0] for field in ("submission_version_id", "total_score")):
        return jsonify({"error": "CSV must include submission_version_id and total_score"}), 400
    records = []
    for row in rows:
        metrics = {key[7:]: value for key, value in row.items() if key.startswith("metric_") and value not in (None, "")}
        records.append({"submission_version_id": row.get("submission_version_id"), "total_score": row.get("total_score") or None, "rank": row.get("rank") or None, "metrics": metrics, "feedback_md": row.get("feedback_md", "")})
    data = {"competition_id": competition_id, "problem_id": problem_id, "source": request.form.get("source", "csv"), "label": request.form.get("label", file.filename), "records": records}
    return persist_score_batch(data, current_user().id)


@submissions_bp.get("/leaderboards/<competition_slug>")
def leaderboard(competition_slug: str):
    competition = Competition.query.filter_by(slug=competition_slug).first_or_404()
    user = current_user()
    if competition.status != "published" and not (user and user.role in {"admin", "organizer"}):
        return jsonify({"error": "competition not found"}), 404
    if (competition.config or {}).get("leaderboard", {}).get("visible") is False:
        return jsonify([])
    if competition.ends_at:
        end_at = competition.ends_at
        if end_at.tzinfo is None:
            end_at = end_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) < end_at:
            return jsonify([])
    track_slug = request.args.get("track")
    query = (
        SubmissionVersion.query
        .join(Submission)
        .join(Problem)
        .join(Track)
        .filter(
            Track.competition_id == competition.id,
            Submission.status.in_(PUBLIC_VERSION_STATUSES),
            SubmissionVersion.status.in_(PUBLIC_VERSION_STATUSES),
            SubmissionVersion.version == Submission.current_version,
        )
    )
    if track_slug:
        query = query.filter(Track.slug == track_slug)
    result = []

    for version in query.order_by(SubmissionVersion.created_at.asc()).all():
        score = combined_score(version)
        if score is None:
            continue
        external = latest_external_score(version)
        metrics = dict(external.metrics or {}) if external else {}
        if score["reviewer_count"]:
            metrics.update({
                "reviewed_count": score["reviewed_count"],
                "reviewer_count": score["reviewer_count"],
            })
        result.append({
            "rank": None,
            "total_score": score["total_score"],
            "metrics": metrics,
            "source": score["source"],
            "submission_id": version.submission_id,
            "batch": external.batch.label if external else "online-review",
            "version": version.version,
            "team": version.submission.team.name,
            "team_members": [{"id": member.user.id, "name": member.user.name} for member in version.submission.team.members],
            "work": version.submission.title,
            "problem": version.submission.problem.code,
            "track": version.submission.problem.track.name,
        })
    result.sort(key=lambda row: row["total_score"] if row["total_score"] is not None else float("-inf"), reverse=True)
    track_ranks = {}
    for index, row in enumerate(result, 1):
        track_ranks[row["track"]] = track_ranks.get(row["track"], 0) + 1
        row["rank"] = track_ranks[row["track"]] if (competition.config or {}).get("leaderboard", {}).get("rank_scope") == "track" else index
    return jsonify(result)
