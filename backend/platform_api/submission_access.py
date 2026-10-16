"""Competition entries are review materials, separate from published community posts."""
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
