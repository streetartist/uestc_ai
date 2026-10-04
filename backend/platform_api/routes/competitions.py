from __future__ import annotations

import secrets
from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, request

from ..extensions import db
from ..models import Competition, Problem, Registration, Submission, Team, TeamMember, Track
from ..reviewing import validate_scoring_config
from ..evaluation import validate_evaluation_config
from ..submission_schema import validate_submission_schema
from ..security import current_user, require_user, team_member
from ..utils import audit, payload


competitions_bp = Blueprint("competitions", __name__)


def can_view_drafts() -> bool:
    user = current_user()
    return bool(user and user.role in {"admin", "organizer"})


def visible_problems(items):
    return [item for item in items if item["status"] != "draft"]


def deadline_passed(value) -> bool:
    if not current_app.config.get("ENFORCE_COMPETITION_DEADLINES", True) or not value:
        return False
    deadline = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    return deadline <= datetime.now(timezone.utc)


def registration_deadline_response():
    return jsonify({"error": "registration deadline has passed"}), 409


@competitions_bp.get("/competitions")
def list_competitions():
    status = request.args.get("status")
    query = Competition.query
    if not can_view_drafts():
        query = query.filter(Competition.status == "published")
    if status:
        query = query.filter_by(status=status)
    return jsonify([item.to_dict() for item in query.order_by(Competition.created_at.desc()).all()])


@competitions_bp.get("/competitions/<slug>")
def competition_detail(slug: str):
    item = Competition.query.filter_by(slug=slug).first_or_404()
    if item.status != "published" and not can_view_drafts():
        return jsonify({"error": "competition not found"}), 404
    data = item.to_dict(include_tracks=True)
    if not can_view_drafts():
        for track in data["tracks"]:
            track["problems"] = visible_problems(track["problems"])
    return jsonify(data)


@competitions_bp.post("/competitions")
@require_user("admin", "organizer")
def create_competition():
    data, error = payload(("slug", "name", "summary"))
    if error:
        return error
    if Competition.query.filter_by(slug=data["slug"]).first():
        return jsonify({"error": "competition slug already exists"}), 409
    item = Competition(slug=data["slug"], name=data["name"], summary=data["summary"], status=data.get("status", "draft"), config=data.get("config", {}))
    db.session.add(item)
    db.session.flush()
    audit("competition.created", "competition", item.id, {"slug": item.slug})
    db.session.commit()
    return jsonify(item.to_dict()), 201


@competitions_bp.post("/competitions/<slug>/tracks")
@require_user("admin", "organizer")
def create_track(slug: str):
    competition = Competition.query.filter_by(slug=slug).first_or_404()
    data, error = payload(("slug", "name"))
    if error:
        return error
    if Track.query.filter_by(competition_id=competition.id, slug=data["slug"]).first():
        return jsonify({"error": "track slug already exists"}), 409
    track = Track(competition=competition, slug=data["slug"], name=data["name"], description=data.get("description", ""), config=data.get("config", {}), position=data.get("position", len(competition.tracks) + 1))
    db.session.add(track)
    db.session.flush()
    audit("track.created", "track", track.id, {"competition": slug})
    db.session.commit()
    return jsonify(track.to_dict()), 201


@competitions_bp.get("/competitions/<competition_slug>/tracks/<track_slug>")
def track_detail(competition_slug: str, track_slug: str):
    competition = Competition.query.filter_by(slug=competition_slug).first_or_404()
    if competition.status != "published" and not can_view_drafts():
        return jsonify({"error": "track not found"}), 404
    track = Track.query.filter_by(competition_id=competition.id, slug=track_slug).first_or_404()
    data = track.to_dict(include_problems=True)
    if not can_view_drafts():
        data["problems"] = visible_problems(data["problems"])
    return jsonify({**data, "competition": competition.to_dict()})


@competitions_bp.post("/tracks/<track_id>/problems")
@require_user("admin", "organizer")
def create_problem(track_id: str):
    track = db.session.get(Track, track_id)
    if not track:
        return jsonify({"error": "track not found"}), 404
    data, error = payload(("code", "slug", "title"))
    if error:
        return error
    try:
        scoring_config = validate_scoring_config(data.get("scoring_config"))
        evaluation_config = validate_evaluation_config(data.get("evaluation_config"))
        data["submission_schema"] = validate_submission_schema(data.get("submission_schema"))
    except ValueError as validation_error:
        return jsonify({"error": str(validation_error)}), 400
    item = Problem(track=track, code=data["code"], slug=data["slug"], title=data["title"], summary=data.get("summary", ""), status=data.get("status", "draft"), statement_md=data.get("statement_md", ""), submission_schema=data.get("submission_schema", {}), judging_schema=data.get("judging_schema", {}), scoring_config=scoring_config, evaluation_config=evaluation_config, difficulty=data.get("difficulty", 3), compute_note=data.get("compute_note", ""), source_url=data.get("source_url"))
    db.session.add(item)
    db.session.flush()
    audit("problem.created", "problem", item.id, {"track_id": track_id})
    db.session.commit()
    return jsonify(item.to_dict(include_statement=True)), 201


@competitions_bp.get("/problems")
def list_problems():
    query = Problem.query.join(Track).join(Competition)
    if not can_view_drafts():
        query = query.filter(Competition.status == "published", Problem.status != "draft")
    if request.args.get("track"):
        query = query.filter(Track.slug == request.args["track"])
    if request.args.get("status"):
        query = query.filter(Problem.status == request.args["status"])
    items = query.order_by(Problem.code).all()
    return jsonify([{**item.to_dict(), "track": item.track.to_dict(), "competition_slug": item.track.competition.slug} for item in items])


@competitions_bp.get("/problems/<problem_id_or_slug>")
def problem_detail(problem_id_or_slug: str):
    item = Problem.query.filter((Problem.id == problem_id_or_slug) | (Problem.slug == problem_id_or_slug)).first_or_404()
    if not can_view_drafts() and (item.status == "draft" or item.track.competition.status != "published"):
        return jsonify({"error": "problem not found"}), 404
    return jsonify({**item.to_dict(include_statement=True), "track": item.track.to_dict(), "competition": item.track.competition.to_dict()})


@competitions_bp.post("/teams")
@require_user()
def create_team():
    user = current_user()
    data, error = payload(("competition_id", "name"))
    if error:
        return error
    competition = db.session.get(Competition, data["competition_id"])
    if not competition:
        return jsonify({"error": "competition not found"}), 404
    if competition.status != "published":
        return jsonify({"error": "competition is not open"}), 409
    if deadline_passed(competition.registration_closes_at):
        return registration_deadline_response()
    if Team.query.filter_by(competition_id=competition.id, name=data["name"]).first():
        return jsonify({"error": "team name already exists"}), 409
    team = Team(competition=competition, name=data["name"].strip(), invite_code=secrets.token_hex(4).upper(), captain_id=user.id)
    team.members.append(TeamMember(user=user))
    db.session.add(team)
    db.session.flush()
    audit("team.created", "team", team.id)
    db.session.commit()
    return jsonify(team.to_dict(include_members=True)), 201


@competitions_bp.post("/teams/join")
@require_user()
def join_team():
    user = current_user()
    data, error = payload(("invite_code",))
    if error:
        return error
    team = Team.query.filter_by(invite_code=data["invite_code"].strip().upper()).first()
    if not team:
        return jsonify({"error": "invalid invite code"}), 404
    if deadline_passed(team.competition.registration_closes_at):
        return registration_deadline_response()
    if not team_member(team.id, user.id):
        db.session.add(TeamMember(team=team, user=user))
        audit("team.joined", "team", team.id)
        db.session.commit()
    return jsonify(team.to_dict(include_members=True))


@competitions_bp.get("/me/teams")
@require_user()
def my_teams():
    user = current_user()
    teams = Team.query.join(TeamMember).filter(TeamMember.user_id == user.id).all()
    return jsonify([team.to_dict(include_members=True) for team in teams])


@competitions_bp.delete("/teams/<team_id>")
@require_user()
def delete_team(team_id: str):
    user = current_user()
    team = db.session.get(Team, team_id)
    if not team:
        return jsonify({"error": "team not found"}), 404
    if team.captain_id != user.id:
        return jsonify({"error": "team captain required"}), 403
    if Registration.query.filter_by(team_id=team.id).first() or Submission.query.filter_by(team_id=team.id).first():
        return jsonify({"error": "team has participation records"}), 409
    audit("team.deleted", "team", team.id, {"name": team.name})
    db.session.delete(team)
    db.session.commit()
    return "", 204


@competitions_bp.get("/me/registrations")
@require_user()
def my_registrations():
    user = current_user()
    items = (
        Registration.query
        .join(Team, Registration.team_id == Team.id)
        .join(TeamMember, TeamMember.team_id == Team.id)
        .filter(TeamMember.user_id == user.id)
        .order_by(Registration.created_at.desc())
        .all()
    )
    tracks = {item.track_id: db.session.get(Track, item.track_id) for item in items if item.track_id}
    teams = {item.team_id: db.session.get(Team, item.team_id) for item in items}
    competitions = {item.competition_id: db.session.get(Competition, item.competition_id) for item in items}
    return jsonify([{
        "id": item.id,
        "competition_id": item.competition_id,
        "competition_name": competitions[item.competition_id].name,
        "track_id": item.track_id,
        "track_name": tracks[item.track_id].name if item.track_id else "全部赛道",
        "track_slug": tracks[item.track_id].slug if item.track_id else None,
        "team_id": item.team_id,
        "team_name": teams[item.team_id].name,
        "status": item.status,
        "fields": item.fields,
    } for item in items])


@competitions_bp.post("/registrations")
@require_user()
def register_team():
    user = current_user()
    data, error = payload(("competition_id", "team_id"))
    if error:
        return error
    if not team_member(data["team_id"], user.id):
        return jsonify({"error": "team membership required"}), 403
    competition = db.session.get(Competition, data["competition_id"])
    team = db.session.get(Team, data["team_id"])
    if not competition or not team:
        return jsonify({"error": "competition or team not found"}), 404
    if competition.status != "published":
        return jsonify({"error": "competition is not open"}), 409
    if deadline_passed(competition.registration_closes_at):
        return registration_deadline_response()
    if team.competition_id != competition.id:
        return jsonify({"error": "team belongs to a different competition"}), 400
    track_id = data.get("track_id")
    if track_id:
        track = db.session.get(Track, track_id)
        if not track or track.competition_id != competition.id:
            return jsonify({"error": "track belongs to a different competition"}), 400
    existing = Registration.query.filter_by(competition_id=competition.id, track_id=track_id, team_id=team.id).first()
    if existing:
        return jsonify({"error": "team already registered"}), 409
    registration = Registration(competition_id=competition.id, track_id=track_id, team_id=team.id, status="confirmed", fields=data.get("fields", {}))
    db.session.add(registration)
    db.session.flush()
    audit("registration.created", "registration", registration.id)
    db.session.commit()
    return jsonify({"id": registration.id, "status": registration.status}), 201


@competitions_bp.delete("/registrations/<registration_id>")
@require_user()
def delete_registration(registration_id: str):
    user = current_user()
    registration = db.session.get(Registration, registration_id)
    if not registration:
        return jsonify({"error": "registration not found"}), 404
    if not team_member(registration.team_id, user.id):
        return jsonify({"error": "team membership required"}), 403
    competition = db.session.get(Competition, registration.competition_id)
    if competition and deadline_passed(competition.registration_closes_at):
        return registration_deadline_response()
    submission_query = Submission.query.join(Problem).join(Track).filter(
        Submission.team_id == registration.team_id,
        Track.competition_id == registration.competition_id,
    )
    if registration.track_id:
        submission_query = submission_query.filter(Problem.track_id == registration.track_id)
    if submission_query.first():
        return jsonify({"error": "registration has submissions"}), 409
    audit("registration.deleted", "registration", registration.id, {"team_id": registration.team_id, "track_id": registration.track_id})
    db.session.delete(registration)
    db.session.commit()
    return "", 204
