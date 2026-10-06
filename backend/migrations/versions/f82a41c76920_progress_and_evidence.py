"""Separate progress records and private trusted evaluation evidence."""
from alembic import op
import sqlalchemy as sa
from migrations.schema_helpers import ensure_index, ensure_table

revision = "f82a41c76920"
down_revision = "e7a19b4c0832"
branch_labels = None
depends_on = None


def upgrade():
    ensure_table("checkpoint_entries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("problem_id", sa.String(36), sa.ForeignKey("problems.id", ondelete="CASCADE"), nullable=False),
        sa.Column("team_id", sa.String(36), sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("checkpoint_id", sa.String(32), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("content_md", sa.Text(), nullable=False),
        sa.Column("repository", sa.String(1000), nullable=False),
        sa.Column("evaluation_run_id", sa.String(36), sa.ForeignKey("evaluation_runs.id", ondelete="SET NULL")),
        sa.Column("package_storage_name", sa.String(255)), sa.Column("package_name", sa.String(255)),
        sa.Column("package_sha256", sa.String(64)), sa.Column("feedback_md", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("problem_id", "team_id", "checkpoint_id", name="uq_checkpoint_team_problem"))
    ensure_index("ix_checkpoint_entries_problem_id", "checkpoint_entries", ["problem_id"])
    ensure_index("ix_checkpoint_entries_team_id", "checkpoint_entries", ["team_id"])
    ensure_table("evaluation_artifacts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("evaluation_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False), sa.Column("storage_name", sa.String(255), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.UniqueConstraint("run_id", "name", name="uq_evaluation_artifact_name"))
    ensure_index("ix_evaluation_artifacts_run_id", "evaluation_artifacts", ["run_id"])


def downgrade():
    op.drop_table("evaluation_artifacts")
    op.drop_table("checkpoint_entries")
