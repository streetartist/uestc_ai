from __future__ import annotations

import uuid
from datetime import datetime, timezone

from .extensions import db


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class User(TimestampMixin, db.Model):
    __tablename__ = "users"
    id = db.Column(db.String(36), primary_key=True, default=new_id)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    name = db.Column(db.String(80), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(24), nullable=False, default="member", index=True)
    email_verified_at = db.Column(db.DateTime(timezone=True))
    sessions = db.relationship("Session", back_populates="user", cascade="all, delete-orphan")
    memberships = db.relationship("TeamMember", back_populates="user", cascade="all, delete-orphan")
    staged_submission_assets = db.relationship("StagedSubmissionAsset", back_populates="uploader", cascade="all, delete-orphan")
    review_assignments = db.relationship("CompetitionReviewer", back_populates="reviewer", cascade="all, delete-orphan")

    def to_dict(self):
        return {"id": self.id, "email": self.email, "name": self.name, "role": self.role, "email_verified": self.email_verified_at is not None}


class Session(db.Model):
    __tablename__ = "sessions"
    token_hash = db.Column(db.String(64), primary_key=True)
    user_id = db.Column(db.String(36), db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    user = db.relationship("User", back_populates="sessions")


class EmailVerificationCode(db.Model):
    __tablename__ = "email_verification_codes"
    id = db.Column(db.String(36), primary_key=True, default=new_id)
    email = db.Column(db.String(255), nullable=False, index=True)
    code_hash = db.Column(db.String(64), nullable=False)
    purpose = db.Column(db.String(32), nullable=False, default="register")
    request_ip_hash = db.Column(db.String(64), nullable=False, index=True)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    consumed_at = db.Column(db.DateTime(timezone=True), index=True)
    attempts = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)


class RegistrationInvite(TimestampMixin, db.Model):
    __tablename__ = "registration_invites"
    id = db.Column(db.String(36), primary_key=True, default=new_id)
    code_hash = db.Column(db.String(64), nullable=False, unique=True, index=True)
    code_prefix = db.Column(db.String(8), nullable=False)
    note = db.Column(db.String(160), nullable=False, default="")
    created_by = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=False, index=True)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    used_at = db.Column(db.DateTime(timezone=True), index=True)
    used_by = db.Column(db.String(36), db.ForeignKey("users.id"), index=True)
    revoked_at = db.Column(db.DateTime(timezone=True), index=True)
    creator = db.relationship("User", foreign_keys=[created_by])
    user = db.relationship("User", foreign_keys=[used_by])

    def status(self):
        now = datetime.now(timezone.utc)
        expires_at = self.expires_at.replace(tzinfo=timezone.utc) if self.expires_at.tzinfo is None else self.expires_at
        if self.revoked_at:
            return "revoked"
        if self.used_at:
            return "used"
        if expires_at <= now:
            return "expired"
        return "active"

    def to_dict(self):
        return {
            "id": self.id,
            "code_prefix": self.code_prefix,
            "note": self.note,
            "status": self.status(),
            "created_by": self.created_by,
            "created_by_name": self.creator.name if self.creator else None,
            "expires_at": iso(self.expires_at),
            "used_at": iso(self.used_at),
            "used_by": self.used_by,
            "used_by_email": self.user.email if self.user else None,
            "created_at": iso(self.created_at),
        }


class Competition(TimestampMixin, db.Model):
    __tablename__ = "competitions"
    id = db.Column(db.String(36), primary_key=True, default=new_id)
    slug = db.Column(db.String(100), unique=True, nullable=False, index=True)
    name = db.Column(db.String(160), nullable=False)
    summary = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(24), nullable=False, default="draft", index=True)
    registration_opens_at = db.Column(db.DateTime(timezone=True))
    registration_closes_at = db.Column(db.DateTime(timezone=True))
    starts_at = db.Column(db.DateTime(timezone=True))
    ends_at = db.Column(db.DateTime(timezone=True))
    config = db.Column(db.JSON, nullable=False, default=dict)
    tracks = db.relationship("Track", back_populates="competition", cascade="all, delete-orphan", order_by="Track.position")
    teams = db.relationship("Team", back_populates="competition", cascade="all, delete-orphan")
    reviewers = db.relationship("CompetitionReviewer", back_populates="competition", cascade="all, delete-orphan")

    def to_dict(self, include_tracks=False):
        data = {"id": self.id, "slug": self.slug, "name": self.name, "summary": self.summary, "status": self.status, "registration_opens_at": iso(self.registration_opens_at), "registration_closes_at": iso(self.registration_closes_at), "starts_at": iso(self.starts_at), "ends_at": iso(self.ends_at), "config": self.config}
        if include_tracks:
            data["tracks"] = [track.to_dict(include_problems=True) for track in self.tracks]
        return data


class Track(TimestampMixin, db.Model):
    __tablename__ = "tracks"
    __table_args__ = (db.UniqueConstraint("competition_id", "slug"),)
    id = db.Column(db.String(36), primary_key=True, default=new_id)
    competition_id = db.Column(db.String(36), db.ForeignKey("competitions.id", ondelete="CASCADE"), nullable=False, index=True)
    slug = db.Column(db.String(100), nullable=False)
    name = db.Column(db.String(160), nullable=False)
    description = db.Column(db.Text, nullable=False, default="")
    config = db.Column(db.JSON, nullable=False, default=dict)
    position = db.Column(db.Integer, nullable=False, default=0)
    competition = db.relationship("Competition", back_populates="tracks")
    problems = db.relationship("Problem", back_populates="track", cascade="all, delete-orphan", order_by="Problem.code")

    def to_dict(self, include_problems=False):
        data = {"id": self.id, "competition_id": self.competition_id, "slug": self.slug, "name": self.name, "description": self.description, "config": self.config, "position": self.position}
        if include_problems:
            data["problems"] = [problem.to_dict() for problem in self.problems]
        return data


class Problem(TimestampMixin, db.Model):
    __tablename__ = "problems"
    __table_args__ = (db.UniqueConstraint("track_id", "slug"), db.UniqueConstraint("track_id", "code"))
    id = db.Column(db.String(36), primary_key=True, default=new_id)
    track_id = db.Column(db.String(36), db.ForeignKey("tracks.id", ondelete="CASCADE"), nullable=False, index=True)
    code = db.Column(db.String(40), nullable=False)
    slug = db.Column(db.String(120), nullable=False)
    title = db.Column(db.String(240), nullable=False)
    summary = db.Column(db.Text, nullable=False, default="")
    status = db.Column(db.String(24), nullable=False, default="draft", index=True)
    statement_md = db.Column(db.Text, nullable=False, default="")
    submission_schema = db.Column(db.JSON, nullable=False, default=dict)
    judging_schema = db.Column(db.JSON, nullable=False, default=dict)
    scoring_config = db.Column(db.JSON, nullable=False, default=dict)
    evaluation_config = db.Column(db.JSON, nullable=False, default=dict)
    difficulty = db.Column(db.Integer, nullable=False, default=3)
    compute_note = db.Column(db.Text, nullable=False, default="")
    source_url = db.Column(db.String(500))
    track = db.relationship("Track", back_populates="problems")
    submissions = db.relationship("Submission", back_populates="problem", cascade="all, delete-orphan")
    evaluation_runs = db.relationship("EvaluationRun", back_populates="problem", cascade="all, delete-orphan")
    ai_quota_policy = db.relationship("AIProblemQuota", back_populates="problem", uselist=False, cascade="all, delete-orphan")
    compute_policy = db.relationship("ComputePolicy", back_populates="problem", uselist=False, cascade="all, delete-orphan")
    runtime = db.relationship("ProblemRuntime", back_populates="problem", uselist=False, cascade="all, delete-orphan")

    def to_dict(self, include_statement=False):
        data = {"id": self.id, "track_id": self.track_id, "code": self.code, "slug": self.slug, "title": self.title, "summary": self.summary, "status": self.status, "difficulty": self.difficulty, "compute_note": self.compute_note, "source_url": self.source_url, "submission_schema": self.submission_schema, "judging_schema": self.judging_schema, "scoring_config": self.scoring_config, "evaluation_config": self.evaluation_config}
        if include_statement:
            data["statement_md"] = self.statement_md
        return data


class Team(TimestampMixin, db.Model):
    __tablename__ = "teams"
    __table_args__ = (db.UniqueConstraint("competition_id", "name"),)
    id = db.Column(db.String(36), primary_key=True, default=new_id)
    competition_id = db.Column(db.String(36), db.ForeignKey("competitions.id", ondelete="CASCADE"), nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    invite_code = db.Column(db.String(20), unique=True, nullable=False, index=True)
    captain_id = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=False)
    competition = db.relationship("Competition", back_populates="teams")
    captain = db.relationship("User", foreign_keys=[captain_id])
    members = db.relationship("TeamMember", back_populates="team", cascade="all, delete-orphan")
    submissions = db.relationship("Submission", back_populates="team", cascade="all, delete-orphan")

    def to_dict(self, include_members=False):
        data = {"id": self.id, "competition_id": self.competition_id, "name": self.name, "invite_code": self.invite_code, "captain_id": self.captain_id}
        if include_members:
            data["members"] = [member.user.to_dict() for member in self.members]
        return data


class TeamMember(db.Model):
    __tablename__ = "team_members"
    team_id = db.Column(db.String(36), db.ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True)
    user_id = db.Column(db.String(36), db.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    joined_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    team = db.relationship("Team", back_populates="members")
    user = db.relationship("User", back_populates="memberships")


class Registration(TimestampMixin, db.Model):
    __tablename__ = "registrations"
    __table_args__ = (db.UniqueConstraint("competition_id", "track_id", "team_id"),)
    id = db.Column(db.String(36), primary_key=True, default=new_id)
    competition_id = db.Column(db.String(36), db.ForeignKey("competitions.id"), nullable=False, index=True)
    track_id = db.Column(db.String(36), db.ForeignKey("tracks.id"), index=True)
    team_id = db.Column(db.String(36), db.ForeignKey("teams.id"), nullable=False, index=True)
    status = db.Column(db.String(24), nullable=False, default="pending")
    fields = db.Column(db.JSON, nullable=False, default=dict)


class Submission(TimestampMixin, db.Model):
    __tablename__ = "submissions"
    __table_args__ = (db.UniqueConstraint("problem_id", "team_id"),)
    id = db.Column(db.String(36), primary_key=True, default=new_id)
    problem_id = db.Column(db.String(36), db.ForeignKey("problems.id"), nullable=False, index=True)
    team_id = db.Column(db.String(36), db.ForeignKey("teams.id"), nullable=False, index=True)
    title = db.Column(db.String(180), nullable=False)
    status = db.Column(db.String(24), nullable=False, default="draft", index=True)
    current_version = db.Column(db.Integer, nullable=False, default=0)
    evaluation_runs_used = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    problem = db.relationship("Problem", back_populates="submissions")
    team = db.relationship("Team", back_populates="submissions")
    versions = db.relationship("SubmissionVersion", back_populates="submission", cascade="all, delete-orphan", order_by="SubmissionVersion.version")

    def to_dict(self, include_versions=False):
        latest_version = self.versions[-1] if self.versions else None
        submitted_versions = [version for version in self.versions if version.status in {"submitted", "confirmed", "published"}]
        data = {
            "id": self.id,
            "problem_id": self.problem_id,
            "problem_slug": self.problem.slug if self.problem else None,
            "team_id": self.team_id,
            "title": self.title,
            "status": self.status,
            "current_version": self.current_version,
            "latest_version_status": latest_version.status if latest_version else None,
            "latest_submitted_version": submitted_versions[-1].version if submitted_versions else None,
            "team_name": self.team.name if self.team else None,
            "problem_code": self.problem.code if self.problem else None,
        }
        if include_versions:
            data["versions"] = [version.to_dict() for version in self.versions]
        return data


class SubmissionVersion(db.Model):
    __tablename__ = "submission_versions"
    __table_args__ = (db.UniqueConstraint("submission_id"),)
    id = db.Column(db.String(36), primary_key=True, default=new_id)
    submission_id = db.Column(db.String(36), db.ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False, index=True)
    version = db.Column(db.Integer, nullable=False)
    readme_md = db.Column(db.Text, nullable=False)
    fields = db.Column(db.JSON, nullable=False, default=dict)
    snapshot = db.Column(db.JSON, nullable=False, default=dict)
    status = db.Column(db.String(24), nullable=False, default="draft")
    created_by = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    submission = db.relationship("Submission", back_populates="versions")
    author = db.relationship("User")
    assets = db.relationship("SubmissionAsset", back_populates="version", cascade="all, delete-orphan")

    def to_dict(self):
        return {"id": self.id, "submission_id": self.submission_id, "version": self.version, "readme_md": self.readme_md, "fields": self.fields, "snapshot": self.snapshot, "status": self.status, "created_by": self.created_by, "created_at": iso(self.created_at)}


class SubmissionAsset(db.Model):
    __tablename__ = "submission_assets"
    id = db.Column(db.String(36), primary_key=True, default=new_id)
    submission_version_id = db.Column(db.String(36), db.ForeignKey("submission_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    original_name = db.Column(db.String(255), nullable=False)
    storage_name = db.Column(db.String(255), unique=True, nullable=False)
    content_type = db.Column(db.String(120), nullable=False)
    size = db.Column(db.Integer, nullable=False)
    visibility = db.Column(db.String(24), nullable=False, default="reviewers")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    version = db.relationship("SubmissionVersion", back_populates="assets")

    def to_dict(self):
        return {"id": self.id, "original_name": self.original_name, "content_type": self.content_type, "size": self.size, "visibility": self.visibility}


class StagedSubmissionAsset(db.Model):
    __tablename__ = "staged_submission_assets"
    id = db.Column(db.String(36), primary_key=True, default=new_id)
    uploaded_by = db.Column(db.String(36), db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    original_name = db.Column(db.String(255), nullable=False)
    storage_name = db.Column(db.String(255), unique=True, nullable=False)
    content_type = db.Column(db.String(120), nullable=False)
    size = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    uploader = db.relationship("User", back_populates="staged_submission_assets")

    def to_dict(self):
        return {
            "id": self.id,
            "original_name": self.original_name,
            "content_type": self.content_type,
            "size": self.size,
            "created_at": iso(self.created_at),
        }


class MarkdownAsset(db.Model):
    __tablename__ = "markdown_assets"
    id = db.Column(db.String(36), primary_key=True, default=new_id)
    uploaded_by = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=False, index=True)
    original_name = db.Column(db.String(255), nullable=False)
    storage_name = db.Column(db.String(255), unique=True, nullable=False)
    content_type = db.Column(db.String(120), nullable=False)
    size = db.Column(db.Integer, nullable=False)
    kind = db.Column(db.String(24), nullable=False)
    visibility = db.Column(db.String(24), nullable=False, default="public", server_default="public")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    uploader = db.relationship("User")

    def to_dict(self):
        return {
            "id": self.id,
            "original_name": self.original_name,
            "content_type": self.content_type,
            "size": self.size,
            "kind": self.kind,
            "url": f"/api/markdown-assets/{self.id}",
        }


class Review(TimestampMixin, db.Model):
    __tablename__ = "reviews"
    __table_args__ = (db.UniqueConstraint("submission_version_id", "reviewer_id"),)
    id = db.Column(db.String(36), primary_key=True, default=new_id)
    submission_version_id = db.Column(db.String(36), db.ForeignKey("submission_versions.id"), nullable=False, index=True)
    reviewer_id = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=False, index=True)
    scores = db.Column(db.JSON, nullable=False, default=dict)
    total_score = db.Column(db.Float)
    feedback_md = db.Column(db.Text, nullable=False, default="")
    status = db.Column(db.String(24), nullable=False, default="draft")


class CompetitionReviewer(TimestampMixin, db.Model):
    __tablename__ = "competition_reviewers"
    __table_args__ = (db.UniqueConstraint("competition_id", "reviewer_id"),)
    id = db.Column(db.String(36), primary_key=True, default=new_id)
    competition_id = db.Column(db.String(36), db.ForeignKey("competitions.id", ondelete="CASCADE"), nullable=False, index=True)
    reviewer_id = db.Column(db.String(36), db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    weight_percent = db.Column(db.Float)
    competition = db.relationship("Competition", back_populates="reviewers")
    reviewer = db.relationship("User", back_populates="review_assignments")

    def to_dict(self):
        return {
            "id": self.id,
            "competition_id": self.competition_id,
            "reviewer_id": self.reviewer_id,
            "weight_percent": self.weight_percent,
        }


class ScoreBatch(TimestampMixin, db.Model):
    __tablename__ = "score_batches"
    id = db.Column(db.String(36), primary_key=True, default=new_id)
    competition_id = db.Column(db.String(36), db.ForeignKey("competitions.id"), nullable=False, index=True)
    problem_id = db.Column(db.String(36), db.ForeignKey("problems.id"), nullable=True, index=True)
    source = db.Column(db.String(80), nullable=False)
    label = db.Column(db.String(160), nullable=False)
    status = db.Column(db.String(24), nullable=False, default="confirmed")
    imported_by = db.Column(db.String(36), db.ForeignKey("users.id"))
    problem = db.relationship("Problem")
    scores = db.relationship("Score", back_populates="batch", cascade="all, delete-orphan")


class Score(db.Model):
    __tablename__ = "scores"
    __table_args__ = (db.UniqueConstraint("batch_id", "submission_version_id"),)
    id = db.Column(db.String(36), primary_key=True, default=new_id)
    batch_id = db.Column(db.String(36), db.ForeignKey("score_batches.id", ondelete="CASCADE"), nullable=False, index=True)
    submission_version_id = db.Column(db.String(36), db.ForeignKey("submission_versions.id"), nullable=False, index=True)
    metrics = db.Column(db.JSON, nullable=False, default=dict)
    total_score = db.Column(db.Float)
    rank = db.Column(db.Integer)
    feedback_md = db.Column(db.Text, nullable=False, default="")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    batch = db.relationship("ScoreBatch", back_populates="scores")
    version = db.relationship("SubmissionVersion")


class EvaluationRun(TimestampMixin, db.Model):
    __tablename__ = "evaluation_runs"
    id = db.Column(db.String(36), primary_key=True, default=new_id)
    problem_id = db.Column(db.String(36), db.ForeignKey("problems.id", ondelete="CASCADE"), nullable=False, index=True)
    submission_version_id = db.Column(db.String(36), db.ForeignKey("submission_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    config_snapshot = db.Column(db.JSON, nullable=False)
    # Never include trusted runtime or private cases in public serializers.
    runtime_snapshot = db.Column(db.JSON(none_as_null=True))
    submission_snapshot = db.Column(db.JSON, nullable=False)
    status = db.Column(db.String(24), nullable=False, default="queued", index=True)
    attempts = db.Column(db.Integer, nullable=False, default=0)
    lease_hash = db.Column(db.String(64))
    api_token_hash = db.Column(db.String(64))
    lease_expires_at = db.Column(db.DateTime(timezone=True))
    metrics = db.Column(db.JSON, nullable=False, default=dict)
    episodes = db.Column(db.JSON, nullable=False, default=list)
    api_calls_used = db.Column(db.Integer, nullable=False, default=0)
    error = db.Column(db.String(500))
    started_at = db.Column(db.DateTime(timezone=True))
    finished_at = db.Column(db.DateTime(timezone=True))
    problem = db.relationship("Problem", back_populates="evaluation_runs")
    submission_version = db.relationship("SubmissionVersion")

    def to_dict(self):
        return {
            "id": self.id, "problem_id": self.problem_id, "submission_version_id": self.submission_version_id,
            "adapter": self.config_snapshot["adapter"], "status": self.status, "attempts": self.attempts,
            "metrics": self.metrics, "episodes": self.episodes, "error": self.error,
            "metric_definitions": self.config_snapshot.get("metric_definitions", []),
            "api_calls_used": self.api_calls_used,
            "time_seconds": self.config_snapshot["resources"]["time_seconds"],
            "created_at": iso(self.created_at), "started_at": iso(self.started_at),
            "finished_at": iso(self.finished_at),
        }


class ProblemRuntime(TimestampMixin, db.Model):
    __tablename__ = "problem_runtimes"
    problem_id = db.Column(db.String(36), db.ForeignKey("problems.id", ondelete="CASCADE"), primary_key=True)
    config = db.Column(db.JSON, nullable=False)
    problem = db.relationship("Problem", back_populates="runtime")


class EvaluationWorkerState(db.Model):
    __tablename__ = "evaluation_worker_states"
    id = db.Column(db.String(80), primary_key=True)
    capabilities = db.Column(db.JSON, nullable=False)
    seen_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)


class Content(TimestampMixin, db.Model):
    __tablename__ = "content"
    id = db.Column(db.String(36), primary_key=True, default=new_id)
    kind = db.Column(db.String(32), nullable=False, index=True)
    slug = db.Column(db.String(140), unique=True, nullable=False, index=True)
    title = db.Column(db.String(240), nullable=False)
    excerpt = db.Column(db.Text, nullable=False, default="")
    body_md = db.Column(db.Text, nullable=False, default="")
    status = db.Column(db.String(24), nullable=False, default="draft", index=True)
    review_note = db.Column(db.Text, nullable=False, default="", server_default="")
    author_id = db.Column(db.String(36), db.ForeignKey("users.id"))
    published_at = db.Column(db.DateTime(timezone=True), index=True)
    author = db.relationship("User")

    def to_dict(self, include_body=False):
        data = {"id": self.id, "kind": self.kind, "slug": self.slug, "title": self.title, "excerpt": self.excerpt, "status": self.status, "author": self.author.name if self.author else None, "author_id": self.author_id, "published_at": iso(self.published_at)}
        if include_body:
            data["body_md"] = self.body_md
            if self.status != "published":
                data["review_note"] = self.review_note
        return data


class AuditLog(db.Model):
    __tablename__ = "audit_log"
    id = db.Column(db.String(36), primary_key=True, default=new_id)
    action = db.Column(db.String(80), nullable=False, index=True)
    entity_type = db.Column(db.String(60), nullable=False)
    entity_id = db.Column(db.String(36), nullable=False)
    actor_id = db.Column(db.String(36), db.ForeignKey("users.id"))
    details = db.Column(db.JSON, nullable=False, default=dict)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)


def iso(value):
    if not value:
        return None
    # SQLite returns naive datetimes; all persisted platform timestamps are UTC.
    aware = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    return aware.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
