from __future__ import annotations

import math

from .extensions import db
from .models import Competition, CompetitionReviewer, Problem, Review, Score, ScoreBatch, SubmissionVersion


REVIEWER_ROLES = {"reviewer", "organizer", "admin"}


def calculate_effective_weights(entries: list[tuple[str, float | None]]) -> dict[str, float]:
    if not entries:
        return {}
    explicit_total = sum(weight for _, weight in entries if weight is not None)
    automatic = [reviewer_id for reviewer_id, weight in entries if weight is None]
    automatic_weight = (100.0 - explicit_total) / len(automatic) if automatic else 0.0
    return {
        reviewer_id: automatic_weight if weight is None else weight
        for reviewer_id, weight in entries
    }


def effective_reviewer_weights(competition_id: str) -> tuple[bool, dict[str, float]]:
    competition = db.session.get(Competition, competition_id)
    assignments = CompetitionReviewer.query.filter_by(competition_id=competition_id).all()
    has_configuration = bool(assignments) or bool((competition.config if competition else {}).get("reviewing"))
    entries = [(assignment.reviewer_id, assignment.weight_percent) for assignment in assignments]
    return has_configuration, calculate_effective_weights(entries)


def is_review_locked(competition: Competition) -> bool:
    reviewing = ((competition.config or {}).get("reviewing") or {})
    return bool(reviewing.get("locked_at"))


def validate_scoring_config(value) -> dict:
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("scoring_config must be an object")
    config = dict(value)
    raw_weight = config.get("external_weight_percent", 0)
    try:
        weight = float(raw_weight)
    except (TypeError, ValueError) as error:
        raise ValueError("external weight must be between 0 and 100 percent") from error
    if weight < 0 or weight > 100:
        raise ValueError("external weight must be between 0 and 100 percent")
    config["external_weight_percent"] = weight
    if config.get("review_score_mode", "sum") not in {"sum", "weighted"}:
        raise ValueError("invalid review score mode")
    if config.get("performance_scoring") is not None:
        from .performance_scoring import validate_performance_scoring
        validate_performance_scoring(config["performance_scoring"])
        if config.get("review_score_mode") != "weighted":
            raise ValueError("自动表现分须配合加权评分。")
    return config


def calculate_review_total(problem: Problem, scores: dict, submitted_total=None) -> float:
    # Existing competitions accept points per criterion; opt in to 0–100 weighted criteria.
    if (problem.scoring_config or {}).get("review_score_mode") != "weighted":
        return submitted_total if submitted_total is not None else sum(float(value) for value in scores.values())
    rubric = (problem.judging_schema or {}).get("rubric", {})
    if not rubric or set(scores) != set(rubric):
        raise ValueError("请为每个评分项填写百分制分数。")
    if any(type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 100 for value in scores.values()):
        raise ValueError("每项分数须在0—100之间。")
    if any(type(weight) not in (int, float) or not math.isfinite(weight) or weight < 0 for weight in rubric.values()) or not math.isclose(sum(rubric.values()), 1):
        raise ValueError("评分权重须合计100%。")
    return sum(scores[key] * weight for key, weight in rubric.items())


def external_weight_percent(problem: Problem) -> float:
    value = ((problem.scoring_config or {}).get("external_weight_percent"))
    if value is None:
        return 0.0
    try:
        return min(max(float(value), 0.0), 100.0)
    except (TypeError, ValueError):
        return 0.0


def latest_external_score(version: SubmissionVersion):
    return (
        Score.query
        .join(ScoreBatch)
        .filter(
            Score.submission_version_id == version.id,
            ScoreBatch.competition_id == version.submission.problem.track.competition_id,
            ScoreBatch.status == "confirmed",
            Score.total_score.isnot(None),
        )
        .order_by(Score.created_at.desc())
        .first()
    )


def review_score(version: SubmissionVersion) -> dict[str, float | int | None] | None:
    """Return a public result only when every assigned reviewer has submitted."""
    _configured, weights = effective_reviewer_weights(version.submission.problem.track.competition_id)
    expected_ids = {reviewer_id for reviewer_id, weight in weights.items() if weight > 0}
    if not expected_ids:
        return None
    reviews = Review.query.filter(
        Review.submission_version_id == version.id,
        Review.reviewer_id.in_(expected_ids),
        Review.status == "submitted",
    ).all()
    by_reviewer = {review.reviewer_id: review for review in reviews}
    if set(by_reviewer) != expected_ids:
        return None
    total = sum(float(by_reviewer[reviewer_id].total_score or 0) * weights[reviewer_id] / 100 for reviewer_id in expected_ids)
    return {
        "total_score": total,
        "reviewed_count": len(expected_ids),
        "reviewer_count": len(expected_ids),
    }


def combined_score(version: SubmissionVersion) -> dict[str, float | int | str | None] | None:
    problem = version.submission.problem
    external_weight = external_weight_percent(problem)
    reviewer_pool_weight = 100.0 - external_weight
    external = latest_external_score(version)
    online = review_score(version) if reviewer_pool_weight > 0 else {"total_score": 0.0, "reviewed_count": 0, "reviewer_count": 0}
    external_available = external is not None
    online_available = online is not None
    if external_weight > 0 and not external_available:
        return None
    if reviewer_pool_weight > 0 and not online_available:
        return None

    total = (float(external.total_score) * external_weight / 100 if external_available else 0.0) + (float(online["total_score"]) * reviewer_pool_weight / 100 if online_available else 0.0)
    if external_available and online_available and external_weight > 0 and reviewer_pool_weight > 0:
        source = "external+online-review"
    elif external_available:
        source = "external"
    else:
        source = "online-review"
    return {
        "total_score": total,
        "source": source,
        "external_score": float(external.total_score) if external_available else None,
        "external_weight_percent": external_weight,
        "reviewed_count": int(online["reviewed_count"]) if online else 0,
        "reviewer_count": int(online["reviewer_count"]) if online else 0,
    }
