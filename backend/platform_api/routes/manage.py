from __future__ import annotations

from datetime import datetime, timedelta, timezone
import secrets

from flask import Blueprint, jsonify, request

from ..extensions import db
from ..ai_models import AIGrant
from ..ai_quotas import lock_competition, lock_problem, remove_unused_grants
from ..models import (
    AuditLog,
    Competition,
    CompetitionReviewer,
    Problem,
    Registration,
    RegistrationInvite,
    Review,
    ScoreBatch,
    Submission,
    SubmissionVersion,
    Team,
    Track,
    User,
)
from ..reviewing import REVIEWER_ROLES, calculate_effective_weights, combined_score, effective_reviewer_weights, external_weight_percent, is_review_locked, latest_external_score, validate_scoring_config
from ..evaluation import validate_evaluation_config
from ..problem_templates import templates
from ..submission_schema import validate_submission_schema
from ..security import current_user, hash_token, require_user
from ..utils import audit


manage_bp = Blueprint("manage", __name__)
COMPETITION_FIELDS = {
    "slug", "name", "summary", "status", "registration_opens_at",
    "registration_closes_at", "starts_at", "ends_at", "config",
}
TRACK_FIELDS = {"slug", "name", "description", "position", "config"}
PROBLEM_FIELDS = {
    "code", "slug", "title", "summary", "status", "statement_md",
    "submission_schema", "judging_schema", "scoring_config", "evaluation_config", "difficulty", "compute_note", "source_url",
}
DATETIME_FIELDS = {"registration_opens_at", "registration_closes_at", "starts_at", "ends_at"}
USER_ROLES = {"member", "reviewer", "editor", "organizer", "admin"}
INVITE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"


def parse_datetime(value):
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ValueError("date must be an ISO string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc)


def update_fields(item, data, allowed_fields):
    for field in allowed_fields:
        if field not in data:
            continue
        value = parse_datetime(data[field]) if field in DATETIME_FIELDS else data[field]
        setattr(item, field, value)


def admin_problem(problem: Problem):
    return problem.to_dict(include_statement=True)


def admin_track(track: Track):
    return {**track.to_dict(), "problems": [admin_problem(problem) for problem in track.problems]}


def admin_competition(competition: Competition):
    return {**competition.to_dict(), "tracks": [admin_track(track) for track in competition.tracks]}


def reviewer_weight_payload(competition: Competition):
    assignments = CompetitionReviewer.query.filter_by(competition_id=competition.id).all()
    reviewing_config = ((competition.config or {}).get("reviewing") or {})
    assignment_by_user = {assignment.reviewer_id: assignment for assignment in assignments}
    configured = bool(assignments) or bool((competition.config or {}).get("reviewing"))
    candidates = User.query.filter(User.role.in_(REVIEWER_ROLES)).order_by(User.name.asc()).all()
    entries = [(assignment.reviewer_id, assignment.weight_percent) for assignment in assignments]
    effective_weights = calculate_effective_weights(entries)

    reviewer_rows = []
    for user in candidates:
        assignment = assignment_by_user.get(user.id)
        selected = bool(assignment)
        reviewer_rows.append({
            **user.to_dict(),
            "selected": selected,
            "weight_percent": assignment.weight_percent if assignment else None,
            "effective_weight_percent": effective_weights.get(user.id, 0) if selected else None,
        })

    versions = (
        SubmissionVersion.query
        .join(Submission)
        .join(Problem)
        .join(Track)
        .filter(
            Track.competition_id == competition.id,
            Submission.status.in_({"submitted", "confirmed", "published"}),
            SubmissionVersion.status.in_({"submitted", "confirmed", "published"}),
            SubmissionVersion.version == Submission.current_version,
        )
        .order_by(SubmissionVersion.created_at.desc())
        .all()
    )
    version_ids = [version.id for version in versions]
    reviews = Review.query.filter(
        Review.submission_version_id.in_(version_ids),
        Review.status == "submitted",
    ).all() if version_ids else []
    reviews_by_version = {}
    for review in reviews:
        reviews_by_version.setdefault(review.submission_version_id, {})[review.reviewer_id] = review

    summaries = []
    for version in versions:
        online_weight = 100 - external_weight_percent(version.submission.problem)
        expected_ids = {reviewer_id for reviewer_id, weight in effective_weights.items() if weight > 0} if online_weight > 0 else set()
        version_reviews = reviews_by_version.get(version.id, {})
        completed_ids = expected_ids & set(version_reviews)
        completed_weight = sum(effective_weights[reviewer_id] for reviewer_id in completed_ids) * online_weight / 100
        weighted_total = sum(float(version_reviews[reviewer_id].total_score or 0) * effective_weights[reviewer_id] / 100 for reviewer_id in completed_ids) * online_weight / 100
        external = latest_external_score(version)
        combined = combined_score(version)
        complete = combined is not None
        summaries.append({
            "submission_version_id": version.id,
            "submission_id": version.submission_id,
            "title": version.submission.title,
            "team_name": version.submission.team.name,
            "problem_code": version.submission.problem.code,
            "version": version.version,
            "reviewed_count": len(completed_ids),
            "reviewer_count": len(expected_ids),
            "completed_weight_percent": completed_weight,
            "external_weight_percent": external_weight_percent(version.submission.problem),
            "external_score": float(external.total_score) if external and external.total_score is not None else None,
            "provisional_score": combined["total_score"] if combined else (weighted_total / (completed_weight / 100) if completed_weight else None),
            "final_score": combined["total_score"] if complete else None,
        })

    return {
        "competition_id": competition.id,
        "configured": configured,
        "locked": is_review_locked(competition),
        "locked_at": reviewing_config.get("locked_at"),
        "locked_by": reviewing_config.get("locked_by"),
        "reviewers": reviewer_rows,
        "review_summaries": summaries,
    }


@manage_bp.get("/manage/catalog")
@require_user("admin", "organizer")
def catalog():
    competitions = Competition.query.order_by(Competition.created_at.desc()).all()
    return jsonify([admin_competition(competition) for competition in competitions])


@manage_bp.patch("/manage/competitions/<competition_id>")
@require_user("admin", "organizer")
def update_competition(competition_id: str):
    competition = db.session.get(Competition, competition_id)
    if not competition:
        return jsonify({"error": "competition not found"}), 404
    data = request.get_json(silent=True) or {}
    if "slug" in data:
        duplicate = Competition.query.filter(Competition.slug == data["slug"], Competition.id != competition.id).first()
        if duplicate:
            return jsonify({"error": "competition slug already exists"}), 409
    try:
        update_fields(competition, data, COMPETITION_FIELDS)
    except (TypeError, ValueError) as error:
        return jsonify({"error": str(error)}), 400
    audit("competition.updated", "competition", competition.id, {"fields": sorted(set(data) & COMPETITION_FIELDS)})
    db.session.commit()
    return jsonify(admin_competition(competition))


@manage_bp.delete("/manage/competitions/<competition_id>")
@require_user("admin", "organizer")
def delete_competition(competition_id: str):
    competition = db.session.get(Competition, competition_id)
    if not competition:
        return jsonify({"error": "competition not found"}), 404
    if (
        Team.query.filter_by(competition_id=competition.id).first()
        or Registration.query.filter_by(competition_id=competition.id).first()
        or ScoreBatch.query.filter_by(competition_id=competition.id).first()
    ):
        return jsonify({"error": "competition has participation records"}), 409
    audit("competition.deleted", "competition", competition.id, {"name": competition.name, "slug": competition.slug})
    db.session.delete(competition)
    db.session.commit()
    return "", 204


@manage_bp.patch("/manage/tracks/<track_id>")
@require_user("admin", "organizer")
def update_track(track_id: str):
    track = db.session.get(Track, track_id)
    if not track:
        return jsonify({"error": "track not found"}), 404
    data = request.get_json(silent=True) or {}
    if "slug" in data:
        duplicate = Track.query.filter(Track.competition_id == track.competition_id, Track.slug == data["slug"], Track.id != track.id).first()
        if duplicate:
            return jsonify({"error": "track slug already exists"}), 409
    update_fields(track, data, TRACK_FIELDS)
    audit("track.updated", "track", track.id, {"fields": sorted(set(data) & TRACK_FIELDS)})
    db.session.commit()
    return jsonify(admin_track(track))


@manage_bp.delete("/manage/tracks/<track_id>")
@require_user("admin", "organizer")
def delete_track(track_id: str):
    track = db.session.get(Track, track_id)
    if not track:
        return jsonify({"error": "track not found"}), 404
    has_submissions = Submission.query.join(Problem).filter(Problem.track_id == track.id).first()
    if Registration.query.filter_by(track_id=track.id).first() or has_submissions:
        return jsonify({"error": "track has participation records"}), 409
    lock_competition(track.competition_id)
    for problem_id in sorted(problem.id for problem in track.problems):
        lock_problem(problem_id)
    from ..compute import remove_unused_compute
    if not remove_unused_compute(problem_ids=[problem.id for problem in track.problems]):
        return jsonify({"error": "track has compute records"}), 409
    if not remove_unused_grants(AIGrant.problem_id.in_([problem.id for problem in track.problems])):
        return jsonify({"error": "track has participation records"}), 409
    audit("track.deleted", "track", track.id, {"name": track.name, "slug": track.slug})
    db.session.delete(track)
    db.session.commit()
    return "", 204


@manage_bp.get("/manage/problem-templates")
@require_user("admin", "organizer")
def problem_templates():
    return jsonify(templates())


@manage_bp.patch("/manage/problems/<problem_id>")
@require_user("admin", "organizer")
def update_problem(problem_id: str):
    problem = db.session.get(Problem, problem_id)
    if not problem:
        return jsonify({"error": "problem not found"}), 404
    data = request.get_json(silent=True) or {}
    if "submission_schema" in data:
        try:
            data["submission_schema"] = validate_submission_schema(data["submission_schema"])
        except ValueError as error:
            return jsonify({"error": str(error)}), 400
    if is_review_locked(problem.track.competition) and ({"judging_schema", "scoring_config"} & set(data)):
        return jsonify({"error": "online review is locked for this competition"}), 409
    if "slug" in data:
        duplicate = Problem.query.filter(Problem.track_id == problem.track_id, Problem.slug == data["slug"], Problem.id != problem.id).first()
        if duplicate:
            return jsonify({"error": "problem slug already exists"}), 409
    if "code" in data:
        duplicate = Problem.query.filter(Problem.track_id == problem.track_id, Problem.code == data["code"], Problem.id != problem.id).first()
        if duplicate:
            return jsonify({"error": "problem code already exists"}), 409
    if "scoring_config" in data:
        try:
            data["scoring_config"] = validate_scoring_config(data["scoring_config"])
        except ValueError as error:
            return jsonify({"error": str(error)}), 400
    if "evaluation_config" in data or "scoring_config" in data:
        from ..evaluation import fixed_evaluation_rules
        lock_problem(problem.id)
        db.session.refresh(problem)
        try:
            data["evaluation_config"] = validate_evaluation_config(data.get("evaluation_config", problem.evaluation_config))
        except ValueError as error:
            return jsonify({"error": str(error)}), 400
        if Submission.query.filter_by(problem_id=problem.id).first() and fixed_evaluation_rules(data["evaluation_config"]) != fixed_evaluation_rules(problem.evaluation_config):
            return jsonify({"error": "evaluation rules cannot change after submissions"}), 409
    update_fields(problem, data, PROBLEM_FIELDS)
    audit("problem.updated", "problem", problem.id, {"fields": sorted(set(data) & PROBLEM_FIELDS)})
    db.session.commit()
    return jsonify(admin_problem(problem))


@manage_bp.delete("/manage/problems/<problem_id>")
@require_user("admin", "organizer")
def delete_problem(problem_id: str):
    problem = db.session.get(Problem, problem_id)
    if not problem:
        return jsonify({"error": "problem not found"}), 404
    if Submission.query.filter_by(problem_id=problem.id).first():
        return jsonify({"error": "problem has submissions"}), 409
    lock_competition(problem.track.competition_id)
    lock_problem(problem.id)
    from ..compute import remove_unused_compute
    if not remove_unused_compute(problem_ids=[problem.id]):
        return jsonify({"error": "problem has compute records"}), 409
    if not remove_unused_grants(AIGrant.problem_id == problem.id):
        return jsonify({"error": "problem has API records"}), 409
    audit("problem.deleted", "problem", problem.id, {"code": problem.code, "title": problem.title})
    db.session.delete(problem)
    db.session.commit()
    return "", 204


@manage_bp.get("/manage/submissions")
@require_user("admin", "organizer")
def managed_submissions():
    competition_id = request.args.get("competition_id")
    query = (
        SubmissionVersion.query
        .join(Submission)
        .join(Problem)
        .join(Track)
        .filter(
            Submission.status.in_({"submitted", "confirmed", "published"}),
            SubmissionVersion.status.in_({"submitted", "confirmed", "published"}),
            SubmissionVersion.version == Submission.current_version,
        )
    )
    if competition_id:
        query = query.filter(Track.competition_id == competition_id)
    versions = query.order_by(SubmissionVersion.created_at.desc()).all()
    return jsonify([{
        **version.submission.to_dict(),
        "version_id": version.id,
        "version_number": version.version,
        "version_status": version.status,
        "version_title": (version.snapshot or {}).get("title") or version.submission.title,
        "version_created_at": version.created_at.isoformat(),
        "track_id": version.submission.problem.track_id,
        "track_name": version.submission.problem.track.name,
        "competition_id": version.submission.problem.track.competition_id,
        "competition_name": version.submission.problem.track.competition.name,
        # Kept as an alias for API clients using the original response shape.
        "latest_version_id": version.id,
        "updated_at": version.created_at.isoformat(),
    } for version in versions])


@manage_bp.get("/manage/score-batches")
@require_user("admin", "organizer")
def score_batches():
    query = ScoreBatch.query
    if request.args.get("competition_id"):
        query = query.filter_by(competition_id=request.args["competition_id"])
    items = query.order_by(ScoreBatch.created_at.desc()).all()
    return jsonify([{
        "id": item.id,
        "competition_id": item.competition_id,
        "problem_id": item.problem_id,
        "problem_code": item.problem.code if item.problem else None,
        "problem_title": item.problem.title if item.problem else None,
        "track_name": item.problem.track.name if item.problem else None,
        "source": item.source,
        "label": item.label,
        "status": item.status,
        "score_count": len(item.scores),
        "created_at": item.created_at.isoformat(),
    } for item in items])


@manage_bp.patch("/manage/score-batches/<batch_id>")
@require_user("admin", "organizer")
def update_score_batch(batch_id: str):
    batch = db.session.get(ScoreBatch, batch_id)
    if not batch:
        return jsonify({"error": "score batch not found"}), 404
    data = request.get_json(silent=True) or {}
    if data.get("status") not in {"draft", "confirmed", "withdrawn"}:
        return jsonify({"error": "invalid score batch status"}), 400
    batch.status = data["status"]
    audit("score_batch.status_changed", "score_batch", batch.id, {"status": batch.status})
    db.session.commit()
    return jsonify({"id": batch.id, "status": batch.status})


@manage_bp.get("/manage/reviewer-weights")
@require_user("admin", "organizer")
def reviewer_weights():
    competition = db.session.get(Competition, request.args.get("competition_id"))
    if not competition:
        return jsonify({"error": "competition not found"}), 404
    return jsonify(reviewer_weight_payload(competition))


@manage_bp.put("/manage/competitions/<competition_id>/reviewers")
@require_user("admin", "organizer")
def update_competition_reviewers(competition_id: str):
    competition = db.session.get(Competition, competition_id)
    if not competition:
        return jsonify({"error": "competition not found"}), 404
    if is_review_locked(competition):
        return jsonify({"error": "online review is locked for this competition"}), 409
    data = request.get_json(silent=True) or {}
    rows = data.get("reviewers")
    if not isinstance(rows, list):
        return jsonify({"error": "reviewers must be a list"}), 400

    user_ids = [row.get("user_id") for row in rows if isinstance(row, dict)]
    if len(user_ids) != len(rows) or any(not isinstance(user_id, str) for user_id in user_ids):
        return jsonify({"error": "reviewers must contain valid user_id values"}), 400
    if len(user_ids) != len(set(user_ids)):
        return jsonify({"error": "reviewer list contains duplicates"}), 400
    users = User.query.filter(User.id.in_(user_ids), User.role.in_(REVIEWER_ROLES)).all()
    if len(users) != len(user_ids):
        return jsonify({"error": "reviewer is not eligible"}), 400

    entries = []
    try:
        for row in rows:
            raw_weight = row.get("weight_percent")
            weight = None if raw_weight in (None, "") else float(raw_weight)
            if weight is not None and (weight <= 0 or weight > 100):
                raise ValueError
            entries.append((row["user_id"], weight))
    except (TypeError, ValueError):
        return jsonify({"error": "reviewer weights must be between 0 and 100"}), 400

    explicit_total = sum(weight for _, weight in entries if weight is not None)
    automatic_count = sum(1 for _, weight in entries if weight is None)
    if explicit_total > 100 + 1e-9:
        return jsonify({"error": "reviewer weights exceed 100 percent"}), 400
    if automatic_count and explicit_total >= 100 - 1e-9:
        return jsonify({"error": "automatic reviewers need remaining weight"}), 400
    if entries and not automatic_count and abs(explicit_total - 100) > 1e-6:
        return jsonify({"error": "reviewer weights must total 100 percent"}), 400

    CompetitionReviewer.query.filter_by(competition_id=competition.id).delete(synchronize_session=False)
    for reviewer_id, weight in entries:
        db.session.add(CompetitionReviewer(
            competition_id=competition.id,
            reviewer_id=reviewer_id,
            weight_percent=weight,
        ))
    # A saved empty list is meaningful: it explicitly disables online reviewers
    # for this competition, which is valid when every problem is externally scored.
    competition_config = dict(competition.config or {})
    reviewing_config = dict(competition_config.get("reviewing") or {})
    reviewing_config["configured"] = True
    competition_config["reviewing"] = reviewing_config
    competition.config = competition_config
    audit("competition.reviewers_updated", "competition", competition.id, {
        "reviewer_count": len(entries),
        "automatic_count": automatic_count,
        "explicit_weight_percent": explicit_total,
    })
    db.session.commit()
    return jsonify(reviewer_weight_payload(competition))


@manage_bp.patch("/manage/competitions/<competition_id>/review-lock")
@require_user("admin", "organizer")
def update_review_lock(competition_id: str):
    competition = db.session.get(Competition, competition_id)
    if not competition:
        return jsonify({"error": "competition not found"}), 404
    data = request.get_json(silent=True) or {}
    if not isinstance(data.get("locked"), bool):
        return jsonify({"error": "locked must be a boolean"}), 400

    actor = current_user()
    competition_config = dict(competition.config or {})
    reviewing_config = dict(competition_config.get("reviewing") or {})
    if data["locked"]:
        reviewing_config["locked_at"] = datetime.now(timezone.utc).isoformat()
        reviewing_config["locked_by"] = actor.id
    else:
        reviewing_config.pop("locked_at", None)
        reviewing_config.pop("locked_by", None)
    reviewing_config["configured"] = True
    competition_config["reviewing"] = reviewing_config
    competition.config = competition_config
    audit(
        "competition.review_locked" if data["locked"] else "competition.review_unlocked",
        "competition",
        competition.id,
        {"locked": data["locked"]},
    )
    db.session.commit()
    return jsonify(reviewer_weight_payload(competition))


@manage_bp.get("/admin/users")
@require_user("admin")
def users():
    query = User.query
    if request.args.get("q"):
        needle = f"%{request.args['q'].strip()}%"
        query = query.filter((User.name.ilike(needle)) | (User.email.ilike(needle)))
    return jsonify([user.to_dict() for user in query.order_by(User.created_at.desc()).all()])


@manage_bp.get("/admin/registration-invites")
@require_user("admin")
def registration_invites():
    items = RegistrationInvite.query.order_by(RegistrationInvite.created_at.desc()).all()
    return jsonify([item.to_dict() for item in items])


@manage_bp.post("/admin/registration-invites")
@require_user("admin")
def create_registration_invite():
    data = request.get_json(silent=True) or {}
    try:
        expires_in_days = int(data.get("expires_in_days", 30))
    except (TypeError, ValueError):
        return jsonify({"error": "invite expiry must be between 1 and 365 days"}), 400
    if expires_in_days < 1 or expires_in_days > 365:
        return jsonify({"error": "invite expiry must be between 1 and 365 days"}), 400
    note = str(data.get("note", "")).strip()
    if len(note) > 160:
        return jsonify({"error": "invite note is too long"}), 400

    code = "".join(secrets.choice(INVITE_ALPHABET) for _ in range(12))
    while RegistrationInvite.query.filter_by(code_hash=hash_token(code)).first():
        code = "".join(secrets.choice(INVITE_ALPHABET) for _ in range(12))
    actor = current_user()
    invite = RegistrationInvite(
        code_hash=hash_token(code),
        code_prefix=code[:4],
        note=note,
        created_by=actor.id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=expires_in_days),
    )
    db.session.add(invite)
    db.session.flush()
    audit("registration_invite.created", "registration_invite", invite.id, {"code_prefix": invite.code_prefix, "expires_in_days": expires_in_days})
    db.session.commit()
    return jsonify({**invite.to_dict(), "code": code}), 201


@manage_bp.delete("/admin/registration-invites/<invite_id>")
@require_user("admin")
def revoke_registration_invite(invite_id: str):
    invite = db.session.get(RegistrationInvite, invite_id)
    if not invite:
        return jsonify({"error": "registration invite not found"}), 404
    if invite.used_at:
        return jsonify({"error": "used registration invite cannot be revoked"}), 409
    if not invite.revoked_at:
        invite.revoked_at = datetime.now(timezone.utc)
        audit("registration_invite.revoked", "registration_invite", invite.id, {"code_prefix": invite.code_prefix})
        db.session.commit()
    return jsonify(invite.to_dict())


@manage_bp.patch("/admin/users/<user_id>")
@require_user("admin")
def update_user(user_id: str):
    user = db.session.get(User, user_id)
    if not user:
        return jsonify({"error": "user not found"}), 404
    data = request.get_json(silent=True) or {}
    role = data.get("role")
    if role not in USER_ROLES:
        return jsonify({"error": "invalid user role"}), 400
    if user.role == "admin" and role != "admin" and User.query.filter_by(role="admin").count() <= 1:
        return jsonify({"error": "cannot remove the last administrator"}), 400
    previous = user.role
    user.role = role
    if role not in REVIEWER_ROLES:
        CompetitionReviewer.query.filter_by(reviewer_id=user.id).delete(synchronize_session=False)
    audit("user.role_changed", "user", user.id, {"from": previous, "to": role}, actor_id=current_user().id)
    db.session.commit()
    return jsonify(user.to_dict())


@manage_bp.get("/admin/audit")
@require_user("admin")
def audit_log():
    limit = min(max(request.args.get("limit", 100, type=int), 1), 500)
    items = AuditLog.query.order_by(AuditLog.created_at.desc()).limit(limit).all()
    actors = {item.actor_id: db.session.get(User, item.actor_id) for item in items if item.actor_id}
    return jsonify([{
        "id": item.id,
        "action": item.action,
        "entity_type": item.entity_type,
        "entity_id": item.entity_id,
        "actor_id": item.actor_id,
        "actor_name": actors[item.actor_id].name if item.actor_id and actors.get(item.actor_id) else None,
        "details": item.details,
        "created_at": item.created_at.isoformat(),
    } for item in items])


@manage_bp.get("/manage/registrations")
@require_user("admin", "organizer")
def managed_registrations():
    query = Registration.query
    if request.args.get("competition_id"):
        query = query.filter_by(competition_id=request.args["competition_id"])
    items = query.order_by(Registration.created_at.desc()).all()
    return jsonify([{
        "id": item.id,
        "competition_id": item.competition_id,
        "track_id": item.track_id,
        "team_id": item.team_id,
        "team_name": db.session.get(Team, item.team_id).name,
        "status": item.status,
        "fields": item.fields,
        "created_at": item.created_at.isoformat(),
    } for item in items])
