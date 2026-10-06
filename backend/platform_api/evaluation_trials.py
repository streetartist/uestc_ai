"""Frozen private trial inputs and matching a selected result to a formal package."""
from __future__ import annotations

import hashlib
from pathlib import Path

from .evaluation import fixed_evaluation_rules
from .security import current_user, team_member


def package_digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def can_view_result(run) -> bool:
    user = current_user()
    if not user:
        return False
    submission = run.submission_version.submission
    if team_member(submission.team_id, user.id) or user.role in {"admin", "organizer"}:
        return True
    # Reviewers see only the result selected in a current formal submission.
    version = run.submission_version
    return user.role == "reviewer" and version.status in {"submitted", "confirmed", "published"} and (
        (version.snapshot or {}).get("evaluation_run_id") == run.id or run.purpose == "submission"
    )


def selected_result_error(run, problem, team_id: str, package_path: Path) -> str | None:
    if not run or run.submission_version.submission.team_id != team_id or run.problem_id != problem.id:
        return "selected evaluation result is not available"
    if run.purpose != "trial" or run.status != "completed":
        return "select a completed evaluation trial"
    if run.submission_snapshot.get("performance_scoring") != (problem.scoring_config or {}).get("performance_scoring"):
        return "performance scoring rules have changed"
    if fixed_evaluation_rules(run.config_snapshot) != {
        **fixed_evaluation_rules(problem.evaluation_config),
        "metric_definitions": run.config_snapshot.get("metric_definitions", []),
    }:
        return "evaluation rules have changed"
    if run.submission_snapshot.get("package_sha256") != package_digest(package_path):
        return "submitted package differs from selected evaluation trial"
    return None
