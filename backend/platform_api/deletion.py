"""Admin deletion of competitions and users.

Every dependent row is removed with explicit bulk deletes in child-first order,
inside one transaction. This works the same on PostgreSQL (strict foreign keys)
and SQLite. Files on disk are removed only after the commit succeeds.
"""
from __future__ import annotations

from pathlib import Path

from flask import current_app
from sqlalchemy import false, func, or_, select, update, delete

from . import public_cache
from .ai_models import AIGrant, AIKey, AIProblemQuota, AIUsage
from .ai_quotas import lock_competition
from .compute import ACTIVE_STATES, STOPPED_STATES
from .compute_models import ComputeAccount, ComputeGrant, ComputeInstance, ComputePolicy, ComputeSession
from .extensions import db
from .judge_models import JudgePool
from .models import (
    AuditLog, Competition, CompetitionReviewer, Content, EmailVerificationCode, EvaluationRun,
    MarkdownAsset, Problem, ProblemRuntime, Registration, RegistrationInvite, Review, Score,
    ScoreBatch, Session as Session_, StagedSubmissionAsset, Submission, SubmissionAsset, SubmissionVersion, Team,
    TeamMember, Track, User,
)
from .progress_models import CheckpointEntry, EvaluationArtifact
from .model_weights import ModelWeightAsset
from .reviewing import is_review_locked
from .utils import audit

# code -> (409 error key shown to API clients, Chinese preview message)
BLOCKERS = {
    "judge_pool": ("赛事已绑定专用 GPU 测评实例，请先停用并解除测评绑定。",
                   "赛事已绑定专用 GPU 测评实例，请先停用并解除测评绑定。"),
    "active_compute_sessions": ("active compute sessions exist", "存在进行中的算力会话，请先停止。"),
    "remote_instance_not_stopped": ("remote compute instances not stopped",
                                    "远端算力实例尚未确认停止，请先在算力管理中停止。"),
    "manual_compute_accounts": ("存在手动发放的算力账号记录，请保留所属队伍或归档赛事。",
                                "存在手动发放的算力账号记录，请保留所属队伍或归档赛事。"),
    "locked_reviews": ("user has reviews in locked competitions",
                       "该用户在已锁定评分的赛事中有已提交评审，无法删除。"),
    "team_in_locked_competition": ("user team in locked or scored competition",
                                   "该用户是唯一成员的队伍位于已锁定评分或已有成绩的赛事，删除会改变成绩。"),
}
USER_RECOMMENDATION = (
    "该系统没有账户停用功能。拥有队伍、作品或评审记录的账户不建议删除；删除不可恢复。"
    "如只需收回权限，请先将其角色改为“普通成员”。"
)


class DeletionBlocked(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code
        self.error = BLOCKERS[code][0]


def _blocker(code):
    return {"code": code, "message": BLOCKERS[code][1]}


def _count(model, *conditions) -> int:
    return db.session.scalar(select(func.count()).select_from(model).where(*conditions)) or 0


class _Scope:
    """Selectors for every row owned by a set of teams and/or problems."""

    def __init__(self, teams, problems=None, competition_id=None):
        self.teams, self.problems, self.competition_id = teams, problems, competition_id
        self.grant_sel = select(AIGrant.id).where(or_(self.T(AIGrant.team_id), self.P(AIGrant.problem_id)))
        self.cgrant_sel = select(ComputeGrant.id).where(or_(self.T(ComputeGrant.team_id), self.P(ComputeGrant.problem_id)))
        self.sub_sel = select(Submission.id).where(or_(self.T(Submission.team_id), self.P(Submission.problem_id)))
        self.ver_sel = select(SubmissionVersion.id).where(SubmissionVersion.submission_id.in_(self.sub_sel))
        self.run_sel = select(EvaluationRun.id).where(or_(
            self.P(EvaluationRun.problem_id), EvaluationRun.submission_version_id.in_(self.ver_sel)))
        self.batch_sel = select(ScoreBatch.id).where(or_(
            ScoreBatch.competition_id == competition_id if competition_id else false(), self.P(ScoreBatch.problem_id)))
        self.ckpt_cond = or_(self.T(CheckpointEntry.team_id), self.P(CheckpointEntry.problem_id),
                             CheckpointEntry.evaluation_run_id.in_(self.run_sel))
        self.score_cond = or_(Score.batch_id.in_(self.batch_sel), Score.submission_version_id.in_(self.ver_sel))
        self.usage_cond = or_(AIUsage.grant_id.in_(self.grant_sel), AIUsage.evaluation_run_id.in_(self.run_sel))

    def T(self, column):
        return column.in_(self.teams)

    def P(self, column):
        return column.in_(self.problems) if self.problems is not None else false()

    def blockers(self):
        found = []
        if _count(ComputeAccount, or_(self.T(ComputeAccount.team_id), self.P(ComputeAccount.problem_id))):
            found.append(_blocker("manual_compute_accounts"))
        if self.problems is not None and _count(JudgePool, JudgePool.problem_id.in_(self.problems)):
            found.append(_blocker("judge_pool"))
        if _count(ComputeSession, ComputeSession.grant_id.in_(self.cgrant_sel), ComputeSession.state.in_(ACTIVE_STATES)):
            found.append(_blocker("active_compute_sessions"))
        if _count(ComputeInstance, ComputeInstance.grant_id.in_(self.cgrant_sel),
                  ComputeInstance.remote_id.is_not(None), ComputeInstance.provider_status.not_in(STOPPED_STATES)):
            found.append(_blocker("remote_instance_not_stopped"))
        return found

    def files(self) -> list[str]:
        statements = (
            select(SubmissionAsset.storage_name).where(SubmissionAsset.submission_version_id.in_(self.ver_sel)),
            select(EvaluationArtifact.storage_name).where(EvaluationArtifact.run_id.in_(self.run_sel)),
            select(EvaluationRun.package_storage_name).where(EvaluationRun.id.in_(self.run_sel)),
            select(CheckpointEntry.package_storage_name).where(self.ckpt_cond),
        )
        names = set()
        for statement in statements:
            names.update(name for name in db.session.scalars(statement) if name)
        for asset in ModelWeightAsset.query.filter(or_(self.T(ModelWeightAsset.team_id), self.P(ModelWeightAsset.problem_id))):
            names.update(asset.file_names())
        return sorted(names)

    def counts(self) -> dict:
        return {
            "teams": len(self.teams) if isinstance(self.teams, list) else _count(Team, Team.id.in_(self.teams)),
            "team_members": _count(TeamMember, self.T(TeamMember.team_id)),
            "submissions": _count(Submission, Submission.id.in_(self.sub_sel)),
            "submission_versions": _count(SubmissionVersion, SubmissionVersion.id.in_(self.ver_sel)),
            "submission_assets": _count(SubmissionAsset, SubmissionAsset.submission_version_id.in_(self.ver_sel)),
            "model_weight_assets": _count(ModelWeightAsset, or_(self.T(ModelWeightAsset.team_id), self.P(ModelWeightAsset.problem_id))),
            "reviews": _count(Review, Review.submission_version_id.in_(self.ver_sel)),
            "score_batches": _count(ScoreBatch, ScoreBatch.id.in_(self.batch_sel)),
            "scores": _count(Score, self.score_cond),
            "evaluation_runs": _count(EvaluationRun, EvaluationRun.id.in_(self.run_sel)),
            "evaluation_artifacts": _count(EvaluationArtifact, EvaluationArtifact.run_id.in_(self.run_sel)),
            "checkpoint_entries": _count(CheckpointEntry, self.ckpt_cond),
            "ai_grants": _count(AIGrant, AIGrant.id.in_(self.grant_sel)),
            "ai_keys": _count(AIKey, AIKey.grant_id.in_(self.grant_sel)),
            "ai_usage_records": _count(AIUsage, self.usage_cond),
            "compute_grants": _count(ComputeGrant, ComputeGrant.id.in_(self.cgrant_sel)),
            "compute_instances": _count(ComputeInstance, ComputeInstance.grant_id.in_(self.cgrant_sel)),
            "compute_sessions": _count(ComputeSession, ComputeSession.grant_id.in_(self.cgrant_sel)),
            "manual_compute_accounts": _count(ComputeAccount, or_(self.T(ComputeAccount.team_id), self.P(ComputeAccount.problem_id))),
        }

    def delete_all(self):
        """Child-first deletes of everything owned by the team/problem scope."""
        def run(statement):
            db.session.execute(statement.execution_options(synchronize_session=False))

        run(delete(AIUsage).where(self.usage_cond))
        run(delete(AIKey).where(AIKey.grant_id.in_(self.grant_sel)))
        run(delete(AIGrant).where(AIGrant.id.in_(self.grant_sel)))
        run(delete(ComputeSession).where(ComputeSession.grant_id.in_(self.cgrant_sel)))
        run(delete(ComputeInstance).where(ComputeInstance.grant_id.in_(self.cgrant_sel)))
        run(delete(ComputeGrant).where(ComputeGrant.id.in_(self.cgrant_sel)))
        run(delete(CheckpointEntry).where(self.ckpt_cond))
        run(delete(EvaluationArtifact).where(EvaluationArtifact.run_id.in_(self.run_sel)))
        run(delete(EvaluationRun).where(EvaluationRun.id.in_(self.run_sel)))
        run(delete(ModelWeightAsset).where(or_(self.T(ModelWeightAsset.team_id), self.P(ModelWeightAsset.problem_id))))
        run(delete(Score).where(self.score_cond))
        run(delete(ScoreBatch).where(ScoreBatch.id.in_(self.batch_sel)))
        run(delete(Review).where(Review.submission_version_id.in_(self.ver_sel)))
        run(delete(SubmissionAsset).where(SubmissionAsset.submission_version_id.in_(self.ver_sel)))
        run(delete(SubmissionVersion).where(SubmissionVersion.id.in_(self.ver_sel)))
        run(delete(Submission).where(Submission.id.in_(self.sub_sel)))
        run(delete(Registration).where(or_(
            Registration.competition_id == self.competition_id if self.competition_id else false(),
            self.T(Registration.team_id))))
        run(delete(TeamMember).where(self.T(TeamMember.team_id)))
        run(delete(Team).where(self.T(Team.id)))


# ---------------------------------------------------------------- competitions

def competition_scope(competition: Competition) -> _Scope:
    problems = select(Problem.id).join(Track, Problem.track_id == Track.id).where(Track.competition_id == competition.id)
    teams = select(Team.id).where(Team.competition_id == competition.id)
    return _Scope(teams, problems, competition.id)


def has_participation(competition: Competition) -> bool:
    scope = competition_scope(competition)
    return bool(
        _count(Team, Team.competition_id == competition.id)
        or _count(Registration, Registration.competition_id == competition.id)
        or _count(ScoreBatch, ScoreBatch.competition_id == competition.id)
        or _count(Submission, Submission.id.in_(scope.sub_sel))
    )


def competition_impact(competition: Competition, actor: User) -> dict:
    scope = competition_scope(competition)
    counts = scope.counts()
    counts["tracks"] = _count(Track, Track.competition_id == competition.id)
    counts["problems"] = _count(Problem, Problem.id.in_(scope.problems))
    counts["registrations"] = _count(Registration, Registration.competition_id == competition.id)
    counts["competition_reviewers"] = _count(CompetitionReviewer, CompetitionReviewer.competition_id == competition.id)
    counts["uploaded_files"] = len(scope.files())
    participation = has_participation(competition)
    blockers = scope.blockers()
    if actor.role != "admin" and participation:
        blockers.append({"code": "admin_required", "message": "仅管理员可以删除已有参赛数据的赛事。"})
    return {
        "competition": {"id": competition.id, "slug": competition.slug, "name": competition.name, "status": competition.status},
        "confirm_text": competition.slug,
        "has_participation": participation,
        "can_delete": not blockers,
        "blockers": blockers,
        "archive": {"available": True, "current_status": competition.status},
        "counts": counts,
    }


def delete_competition_cascade(competition: Competition, actor: User) -> None:
    lock_competition(competition.id)
    scope = competition_scope(competition)
    blockers = scope.blockers()
    if blockers:
        raise DeletionBlocked(blockers[0]["code"])
    counts, names = scope.counts(), scope.files()
    details = {
        "id": competition.id, "slug": competition.slug, "name": competition.name, "status": competition.status,
        "counts": counts, "files_planned": len(names),
        "actor": {"id": actor.id, "name": actor.name, "email": actor.email},
    }
    scope.delete_all()
    for model in (CompetitionReviewer,):
        db.session.execute(delete(model).where(model.competition_id == competition.id).execution_options(synchronize_session=False))
    for model in (AIProblemQuota, ComputePolicy, ProblemRuntime):
        db.session.execute(delete(model).where(model.problem_id.in_(scope.problems)).execution_options(synchronize_session=False))
    db.session.execute(delete(Problem).where(Problem.id.in_(scope.problems)).execution_options(synchronize_session=False))
    db.session.execute(delete(Track).where(Track.competition_id == competition.id).execution_options(synchronize_session=False))
    db.session.execute(delete(Competition).where(Competition.id == competition.id).execution_options(synchronize_session=False))
    audit("competition.deleted", "competition", competition.id, details, actor_id=actor.id)
    db.session.commit()
    _after_commit(names)


# ----------------------------------------------------------------------- users

def _user_plan(user: User) -> dict:
    uid = user.id
    team_ids = set(db.session.scalars(select(TeamMember.team_id).where(TeamMember.user_id == uid)))
    team_ids |= set(db.session.scalars(select(Team.id).where(Team.captain_id == uid)))
    deleted, transfer, removed = [], {}, []
    for team_id in sorted(team_ids):
        others = db.session.execute(
            select(TeamMember.user_id).where(TeamMember.team_id == team_id, TeamMember.user_id != uid)
            .order_by(TeamMember.joined_at, TeamMember.user_id)).scalars().all()
        if not others:
            deleted.append(team_id)
        elif db.session.get(Team, team_id).captain_id == uid:
            transfer[team_id] = others[0]
        else:
            removed.append(team_id)
    return {"deleted": deleted, "transfer": transfer, "removed": removed}


def _locked_review_count(uid: str) -> int:
    rows = db.session.execute(
        select(Track.competition_id, func.count(Review.id))
        .select_from(Review)
        .join(SubmissionVersion, Review.submission_version_id == SubmissionVersion.id)
        .join(Submission, SubmissionVersion.submission_id == Submission.id)
        .join(Problem, Submission.problem_id == Problem.id)
        .join(Track, Problem.track_id == Track.id)
        .where(Review.reviewer_id == uid, Review.status == "submitted")
        .group_by(Track.competition_id)).all()
    total = 0
    for competition_id, amount in rows:
        competition = db.session.get(Competition, competition_id)
        if competition and is_review_locked(competition):
            total += amount
    return total


def _locked_or_scored_team(scope: _Scope, team_ids: list[str]) -> bool:
    if not team_ids:
        return False
    for competition_id in db.session.scalars(select(Team.competition_id).where(Team.id.in_(team_ids)).distinct()):
        competition = db.session.get(Competition, competition_id)
        if competition and is_review_locked(competition):
            return True
    return bool(_count(Score, Score.submission_version_id.in_(scope.ver_sel)))


def user_impact(user: User, actor: User) -> dict:
    uid = user.id
    plan = _user_plan(user)
    scope = _Scope(plan["deleted"])
    own_counts = scope.counts() if plan["deleted"] else {}
    keep_teams = [team_id for team_id in db.session.scalars(
        select(Submission.team_id).join(SubmissionVersion).where(SubmissionVersion.created_by == uid).distinct())
        if team_id not in plan["deleted"]]
    staged = list(db.session.scalars(select(StagedSubmissionAsset.storage_name).where(StagedSubmissionAsset.uploaded_by == uid)))
    locked_reviews = _locked_review_count(uid)
    blockers = scope.blockers() if plan["deleted"] else []
    if locked_reviews:
        blockers.append(_blocker("locked_reviews"))
    if _locked_or_scored_team(scope, plan["deleted"]):
        blockers.append(_blocker("team_in_locked_competition"))
    admins = _count(User, User.role == "admin")
    is_self = user.id == actor.id
    kept_versions = _count(SubmissionVersion, SubmissionVersion.created_by == uid,
                           SubmissionVersion.submission_id.in_(select(Submission.id).where(Submission.team_id.in_(keep_teams)))) if keep_teams else 0
    files = sorted(set(scope.files() if plan["deleted"] else []) | set(staged))
    return {
        "user": {"id": user.id, "email": user.email, "name": user.name, "role": user.role},
        "confirm_text": user.email,
        "guards": {"is_self": is_self, "is_last_admin": user.role == "admin" and admins <= 1},
        "can_delete": not blockers and not is_self and not (user.role == "admin" and admins <= 1),
        "blockers": blockers,
        "recommendation": USER_RECOMMENDATION,
        "counts": {
            "sessions": _count(Session_, Session_.user_id == uid),
            "team_memberships": _count(TeamMember, TeamMember.user_id == uid),
            "teams_deleted": len(plan["deleted"]),
            "teams_captain_transferred": len(plan["transfer"]),
            "teams_member_removed": len(plan["removed"]) + len(plan["transfer"]),
            "submissions_deleted": own_counts.get("submissions", 0),
            "submission_versions_authored": _count(SubmissionVersion, SubmissionVersion.created_by == uid),
            "submission_versions_reassigned": kept_versions,
            "reviews": _count(Review, Review.reviewer_id == uid),
            "submitted_reviews_in_locked_competitions": locked_reviews,
            "competition_reviewer_assignments": _count(CompetitionReviewer, CompetitionReviewer.reviewer_id == uid),
            "score_batches_imported": _count(ScoreBatch, ScoreBatch.imported_by == uid),
            "ai_keys_created": _count(AIKey, AIKey.created_by == uid),
            "ai_keys_disabled": _count(AIKey, AIKey.created_by == uid, AIKey.grant_id.in_(
                select(AIGrant.id).where(AIGrant.team_id.not_in(plan["deleted"])))),
            "compute_sessions_requested": _count(ComputeSession, ComputeSession.requested_by == uid),
            "compute_resources_deleted": own_counts.get("compute_grants", 0) + own_counts.get("compute_instances", 0) + own_counts.get("compute_sessions", 0),
            "contents_authored": _count(Content, Content.author_id == uid),
            "markdown_assets_reassigned": _count(MarkdownAsset, MarkdownAsset.uploaded_by == uid),
            "invites_created": _count(RegistrationInvite, RegistrationInvite.created_by == uid),
            "invites_used": _count(RegistrationInvite, RegistrationInvite.used_by == uid),
            "staged_assets": len(staged),
            "uploaded_files": len(files),
            "audit_rows_referencing_user": _count(AuditLog, AuditLog.actor_id == uid),
        },
    }


def delete_user_cascade(user: User, actor: User) -> None:
    uid = user.id
    plan = _user_plan(user)
    # Lock the competitions of teams that will be removed, in id order.
    if plan["deleted"]:
        for competition_id in sorted(set(db.session.scalars(select(Team.competition_id).where(Team.id.in_(plan["deleted"]))))):
            lock_competition(competition_id)
    scope = _Scope(plan["deleted"])
    impact = user_impact(user, actor)
    if impact["blockers"]:
        raise DeletionBlocked(impact["blockers"][0]["code"])
    names = sorted(set(scope.files() if plan["deleted"] else [])
                   | set(db.session.scalars(select(StagedSubmissionAsset.storage_name).where(StagedSubmissionAsset.uploaded_by == uid))))
    snapshot = {"id": user.id, "name": user.name, "email": user.email}
    details = {
        "id": user.id, "email": user.email, "name": user.name, "role": user.role,
        "counts": impact["counts"], "files_planned": len(names),
        "decisions": {"teams_deleted": len(plan["deleted"]), "captain_transferred": len(plan["transfer"])},
        "actor": {"id": actor.id, "name": actor.name, "email": actor.email},
    }
    new_captain = dict(plan["transfer"])

    def run(statement):
        db.session.execute(statement.execution_options(synchronize_session=False))

    # Teams that keep other members: transfer leadership, then drop this member.
    for team_id, captain_id in plan["transfer"].items():
        run(update(Team).where(Team.id == team_id).values(captain_id=captain_id))

    def owner_of(team_id):
        return new_captain.get(team_id) or db.session.scalar(select(Team.captain_id).where(Team.id == team_id))

    for team_id in set(db.session.scalars(select(Submission.team_id).join(SubmissionVersion).where(SubmissionVersion.created_by == uid))):
        if team_id not in plan["deleted"]:
            run(update(SubmissionVersion).where(
                SubmissionVersion.created_by == uid,
                SubmissionVersion.submission_id.in_(select(Submission.id).where(Submission.team_id == team_id)),
            ).values(created_by=owner_of(team_id)))
    for team_id in set(db.session.scalars(select(AIGrant.team_id).join(AIKey, AIKey.grant_id == AIGrant.id).where(AIKey.created_by == uid))):
        if team_id not in plan["deleted"]:
            run(update(AIKey).where(AIKey.created_by == uid, AIKey.grant_id.in_(
                select(AIGrant.id).where(AIGrant.team_id == team_id))).values(created_by=owner_of(team_id), enabled=False))
    for team_id in set(db.session.scalars(select(ComputeGrant.team_id).join(ComputeSession, ComputeSession.grant_id == ComputeGrant.id).where(ComputeSession.requested_by == uid))):
        if team_id not in plan["deleted"]:
            run(update(ComputeSession).where(ComputeSession.requested_by == uid, ComputeSession.grant_id.in_(
                select(ComputeGrant.id).where(ComputeGrant.team_id == team_id))).values(requested_by=owner_of(team_id)))

    if plan["deleted"]:
        scope.delete_all()
    run(delete(Review).where(Review.reviewer_id == uid))
    run(delete(TeamMember).where(TeamMember.user_id == uid))
    run(delete(CompetitionReviewer).where(CompetitionReviewer.reviewer_id == uid))
    run(delete(StagedSubmissionAsset).where(StagedSubmissionAsset.uploaded_by == uid))
    run(delete(Session_).where(Session_.user_id == uid))
    run(delete(EmailVerificationCode).where(EmailVerificationCode.email == user.email.strip().lower()))
    run(update(MarkdownAsset).where(MarkdownAsset.uploaded_by == uid).values(uploaded_by=actor.id))
    run(update(RegistrationInvite).where(RegistrationInvite.created_by == uid).values(created_by=actor.id))
    run(update(RegistrationInvite).where(RegistrationInvite.used_by == uid).values(used_by=None))
    run(update(ScoreBatch).where(ScoreBatch.imported_by == uid).values(imported_by=None))
    run(update(Content).where(Content.author_id == uid).values(author_id=None))
    for row in AuditLog.query.filter_by(actor_id=uid).all():
        row.details = {**(row.details or {}), "actor_snapshot": snapshot}
        row.actor_id = None
    db.session.flush()
    run(delete(User).where(User.id == uid))
    audit("user.deleted", "user", uid, details, actor_id=actor.id)
    db.session.commit()
    _after_commit(names)


# ----------------------------------------------------------------------- files

def _after_commit(names: list[str]) -> None:
    public_cache.invalidate_public_cache()
    remove_upload_files(names)


def _still_referenced(name: str) -> bool:
    checks = (
        (SubmissionAsset, SubmissionAsset.storage_name), (StagedSubmissionAsset, StagedSubmissionAsset.storage_name),
        (MarkdownAsset, MarkdownAsset.storage_name), (EvaluationArtifact, EvaluationArtifact.storage_name),
        (EvaluationRun, EvaluationRun.package_storage_name), (CheckpointEntry, CheckpointEntry.package_storage_name),
        (ModelWeightAsset, ModelWeightAsset.storage_name),
    )
    return any(_count(model, column == name) for model, column in checks)


def remove_upload_files(names) -> dict:
    """Best-effort cleanup after commit; never raises and never leaves UPLOAD_FOLDER."""
    result = {"removed": 0, "missing": 0, "skipped": 0}
    root = Path(current_app.config["UPLOAD_FOLDER"]).resolve()
    for name in names:
        try:
            if (not isinstance(name, str) or not name or "/" in name or "\\" in name or ".." in name
                    or Path(name).is_absolute() or name.startswith("markdown-")):
                current_app.logger.warning("Skipping unsafe upload name during deletion")
                result["skipped"] += 1
                continue
            target = (root / name).resolve()
            if target.parent != root or _still_referenced(name):
                result["skipped"] += 1
                continue
            if target.exists():
                target.unlink(missing_ok=True)
                result["removed"] += 1
            else:
                result["missing"] += 1
        except OSError:
            current_app.logger.warning("Could not delete upload file during cleanup", exc_info=True)
            result["skipped"] += 1
        except Exception:  # cleanup must never turn a committed deletion into an error
            current_app.logger.warning("Unexpected error during upload cleanup", exc_info=True)
            result["skipped"] += 1
    current_app.logger.info("Upload cleanup finished: %s", result)
    return result

