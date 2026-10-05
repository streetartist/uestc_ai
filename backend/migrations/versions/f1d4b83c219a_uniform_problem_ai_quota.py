"""Persist a single AI quota policy per problem for existing and future teams."""
from alembic import op
import sqlalchemy as sa

revision = "f1d4b83c219a"
down_revision = "e9c3a72b108f"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("ai_problem_quotas",
        sa.Column("problem_id", sa.String(36), sa.ForeignKey("problems.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))


def downgrade():
    if op.get_bind().execute(sa.text("SELECT problem_id FROM ai_problem_quotas LIMIT 1")).first():
        raise RuntimeError("Cannot downgrade while uniform problem AI quota policies exist; future teams would lose their allocation.")
    op.drop_table("ai_problem_quotas")
