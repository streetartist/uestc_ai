"""Private judging materials and explicitly approved post-competition showcases."""
from datetime import datetime, timezone

from .models import CompetitionReviewer, Submission, SubmissionVersion
from .security import current_user, team_member

FORMAL_STATUSES = {"submitted", "confirmed", "published"}


def can_view_submission(submission: Submission, version: SubmissionVersion | None = None) -> bool:
    user = current_user()
    if not user:
        return False
    if user.role in {"admin", "organizer"} or team_member(submission.team_id, user.id):
        return True
    version = version or (submission.versions[-1] if submission.versions else None)
    return bool(user.role == "reviewer" and version and submission.versions
                and version.id == submission.versions[-1].id
                and submission.status in FORMAL_STATUSES and version.status in FORMAL_STATUSES
                and CompetitionReviewer.query.filter_by(
                    competition_id=submission.problem.track.competition_id, reviewer_id=user.id).first())


def competition_ended(competition) -> bool:
    end = competition.ends_at
    if end is None:
        return False
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    return end <= datetime.now(timezone.utc)


def is_work_public(submission, version=None) -> bool:
    version = version or (submission.versions[-1] if submission.versions else None)
    publication = submission.publication or {}
    competition = submission.problem.track.competition
    return bool(version and submission.versions and version.id == submission.versions[-1].id
                and submission.status in FORMAL_STATUSES and version.status in FORMAL_STATUSES
                and publication.get("published") is True and publication.get("version_id") == version.id
                and competition.status == "published" and submission.problem.status == "published"
                and competition_ended(competition))


def can_view_work(submission, version=None) -> bool:
    return can_view_submission(submission, version) or is_work_public(submission, version)


def can_download_asset(asset) -> bool:
    submission = asset.version.submission
    return can_view_submission(submission, asset.version) or (
        is_work_public(submission, asset.version)
        and asset.id in (submission.publication or {}).get("asset_ids", []))
