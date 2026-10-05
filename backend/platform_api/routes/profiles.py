from flask import Blueprint, jsonify
from ..extensions import db
from ..models import Competition, Content, Registration, Submission, Team, TeamMember, Track, User
from .submissions import PUBLIC_VERSION_STATUSES, current_version, public_work_payload

profiles_bp = Blueprint("profiles", __name__)


@profiles_bp.get("/profiles/<user_id>")
def profile(user_id):
    user = db.session.get(User, user_id)
    if not user:
        return jsonify({"error": "user not found"}), 404
    history = (db.session.query(Registration, Competition, Team)
               .join(Competition, Registration.competition_id == Competition.id)
               .join(Team, Registration.team_id == Team.id)
               .join(TeamMember, TeamMember.team_id == Team.id)
               .filter(TeamMember.user_id == user.id, Registration.status == "confirmed", Competition.status == "published")
               .order_by(Registration.created_at.desc()).all())
    competitions = []
    for registration, competition, team in history:
        track = db.session.get(Track, registration.track_id) if registration.track_id else None
        competitions.append({"id": registration.id, "competition_name": competition.name, "competition_slug": competition.slug,
                             "track_name": track.name if track else "全部赛道", "team_name": team.name})
    submissions = Submission.query.join(TeamMember, TeamMember.team_id == Submission.team_id).filter(TeamMember.user_id == user.id, Submission.status.in_(PUBLIC_VERSION_STATUSES)).order_by(Submission.updated_at.desc()).all()
    works = []
    for submission in submissions:
        version = current_version(submission)
        if version and version.status in PUBLIC_VERSION_STATUSES and submission.problem.track.competition.status == "published" and submission.problem.status != "draft":
            works.append(public_work_payload(version, include_content=False))
    contributions = Content.query.filter_by(author_id=user.id, status="published").order_by(Content.published_at.desc()).all()
    # Public identity is deliberately separate from User.to_dict: no email, role or private records.
    return jsonify({"id": user.id, "name": user.name, "joined_at": user.created_at.isoformat(),
                    "competitions": competitions, "works": works, "contributions": [item.to_dict() for item in contributions]})
