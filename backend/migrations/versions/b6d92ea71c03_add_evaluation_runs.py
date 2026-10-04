"""Add configurable evaluation and durable run records.

Revision ID: b6d92ea71c03
Revises: e7b9c1d3a5f2
"""
from alembic import op
import sqlalchemy as sa


revision = "b6d92ea71c03"
down_revision = "e7b9c1d3a5f2"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("problems") as batch_op:
        batch_op.add_column(sa.Column("evaluation_config", sa.JSON(), nullable=False, server_default="{}"))
    op.create_table(
        "evaluation_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("problem_id", sa.String(36), sa.ForeignKey("problems.id", ondelete="CASCADE"), nullable=False),
        sa.Column("submission_version_id", sa.String(36), sa.ForeignKey("submission_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("config_snapshot", sa.JSON(), nullable=False),
        sa.Column("submission_snapshot", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("lease_hash", sa.String(64)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("episodes", sa.JSON(), nullable=False),
        sa.Column("api_calls_used", sa.Integer(), nullable=False),
        sa.Column("error", sa.String(500)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    with op.batch_alter_table("evaluation_runs") as batch_op:
        batch_op.create_index("ix_evaluation_runs_problem_id", ["problem_id"])
        batch_op.create_index("ix_evaluation_runs_submission_version_id", ["submission_version_id"])
        batch_op.create_index("ix_evaluation_runs_status", ["status"])


def downgrade():
    op.drop_table("evaluation_runs")
    with op.batch_alter_table("problems") as batch_op:
        batch_op.drop_column("evaluation_config")
