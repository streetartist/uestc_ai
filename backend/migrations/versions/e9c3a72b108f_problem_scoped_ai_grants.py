"""Allow a separate AI budget for each problem and team.

Revision ID: e9c3a72b108f
Revises: d8a2f19c630e
"""
from alembic import op
import sqlalchemy as sa

revision = "e9c3a72b108f"
down_revision = "d8a2f19c630e"
branch_labels = None
depends_on = None


def upgrade():
    # The original team-only constraint was unnamed. A naming convention also
    # makes SQLite's batch recreation address it without touching existing rows.
    old_unique = next(c for c in sa.inspect(op.get_bind()).get_unique_constraints("ai_grants") if c["column_names"] == ["team_id"])
    with op.batch_alter_table("ai_grants", naming_convention={"uq": "uq_%(table_name)s_%(column_0_name)s"}) as batch:
        batch.drop_constraint(old_unique["name"] or "uq_ai_grants_team_id", type_="unique")
        batch.add_column(sa.Column("problem_id", sa.String(36), nullable=True))
        batch.create_foreign_key("fk_ai_grants_problem_id", "problems", ["problem_id"], ["id"])
        batch.create_unique_constraint("uq_ai_grants_team_problem", ["team_id", "problem_id"])
        batch.create_index("ix_ai_grants_problem_id", ["problem_id"])
    op.create_index("uq_ai_grants_team_default", "ai_grants", ["team_id"], unique=True,
        sqlite_where=sa.text("problem_id IS NULL"), postgresql_where=sa.text("problem_id IS NULL"))


def downgrade():
    connection = op.get_bind()
    # A team may have several independently spent budgets now. Never merge,
    # delete, or reassign them silently just to make an old schema fit.
    scoped = connection.execute(sa.text(
        "SELECT id FROM ai_grants WHERE problem_id IS NOT NULL LIMIT 1"
    )).first()
    if scoped:
        raise RuntimeError("Cannot downgrade while problem-scoped AI budgets exist; export and reconcile them first.")
    op.drop_index("uq_ai_grants_team_default", table_name="ai_grants")
    with op.batch_alter_table("ai_grants") as batch:
        batch.drop_index("ix_ai_grants_problem_id")
        batch.drop_constraint("uq_ai_grants_team_problem", type_="unique")
        batch.drop_constraint("fk_ai_grants_problem_id", type_="foreignkey")
        batch.drop_column("problem_id")
        batch.create_unique_constraint("uq_ai_grants_team_id", ["team_id"])
