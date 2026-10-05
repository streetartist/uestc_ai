"""A problem has one policy; each team keeps its own budget and usage records."""
from sqlalchemy import update

from .ai_models import AIGrant, AIKey, AIProblemQuota, AIUsage
from .extensions import db
from .models import Competition, Problem, Track


def lock_competition(competition_id):
    # Include the no-policy/no-team case so concurrent first saves and team
    # creation cannot both miss each other's newly created record.
    db.session.execute(update(Competition).where(Competition.id == competition_id).values(
        updated_at=Competition.updated_at).execution_options(synchronize_session=False))


def lock_problem(problem_id):
    # An UPDATE acquires a write lock on SQLite and a row lock on PostgreSQL.
    # Save and team creation use the same lock before reading the current policy.
    db.session.execute(update(Problem).where(Problem.id == problem_id).values(
        updated_at=Problem.updated_at).execution_options(synchronize_session=False))


def apply_quota(grant, config):
    for name, value in config.items():
        setattr(grant, name, value)


def remove_unused_grants(*conditions):
    """Empty automatic allocations may be removed; keys and usage are history."""
    grants = AIGrant.query.filter(*conditions).order_by(AIGrant.id).with_for_update().all()
    ids = [grant.id for grant in grants]
    if (any(grant.calls_used or grant.tokens_used or grant.cost_used_micros for grant in grants)
            or AIKey.query.filter(AIKey.grant_id.in_(ids)).first()
            or AIUsage.query.filter(AIUsage.grant_id.in_(ids)).first()):
        return False
    for grant in grants:
        db.session.delete(grant)
    db.session.flush()
    return True


def provision_team_quotas(team):
    """Called inside the team creation transaction, including disabled policies."""
    problem_ids = db.session.query(AIProblemQuota.problem_id).join(Problem).join(Track).filter(
        Track.competition_id == team.competition_id).order_by(AIProblemQuota.problem_id).all()
    for (problem_id,) in problem_ids:
        lock_problem(problem_id)
        policy = db.session.get(AIProblemQuota, problem_id, populate_existing=True)
        if not policy:
            continue
        grant = AIGrant.query.filter_by(team_id=team.id, problem_id=problem_id).first()
        if not grant:
            grant = AIGrant(team_id=team.id, problem_id=problem_id)
            apply_quota(grant, policy.config)
            db.session.add(grant)
