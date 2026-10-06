"""Durable organizer AutoDL scheduling and one-time quota refunds."""
from alembic import op
import sqlalchemy as sa
from migrations.schema_helpers import ensure_index, ensure_table

revision = "a913be2c70dd"
down_revision = "f82a41c76920"
branch_labels = None
depends_on = None


def upgrade():
    ensure_table("evaluation_judge_pools",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("problem_id", sa.String(36), sa.ForeignKey("problems.id"), nullable=False, unique=True),
        sa.Column("provider_id", sa.String(36), sa.ForeignKey("compute_providers.id"), nullable=False),
        sa.Column("remote_id", sa.String(160), nullable=False, unique=True),
        sa.Column("image", sa.String(160), nullable=False),
        sa.Column("worker_id", sa.String(80), nullable=False, unique=True),
        sa.Column("dataset_manifests", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("idle_seconds", sa.Integer(), nullable=False),
        sa.Column("boot_seconds", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("provider_status", sa.String(32), nullable=False),
        sa.Column("error", sa.String(300)),
        *[sa.Column(name, sa.DateTime(timezone=True)) for name in (
            "checked_at", "dispatched_at", "idle_since", "retry_at", "lease_expires_at")],
        sa.Column("lease_owner", sa.String(80)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    columns = {column['name'] for column in sa.inspect(op.get_bind()).get_columns('evaluation_runs')}
    with op.batch_alter_table("evaluation_runs") as batch:
        if 'judge_pool_id' not in columns:
            batch.add_column(sa.Column("judge_pool_id", sa.String(36)))
            batch.create_foreign_key("fk_evaluation_run_judge_pool", "evaluation_judge_pools", ["judge_pool_id"], ["id"])
        if 'quota_refunded' not in columns:
            batch.add_column(sa.Column("quota_refunded", sa.Boolean(), nullable=False, server_default=sa.false()))
    ensure_index("ix_evaluation_runs_judge_pool_id", "evaluation_runs", ["judge_pool_id"])


def downgrade():
    with op.batch_alter_table("evaluation_runs") as batch:
        batch.drop_index("ix_evaluation_runs_judge_pool_id")
        batch.drop_constraint("fk_evaluation_run_judge_pool", type_="foreignkey")
        batch.drop_column("judge_pool_id")
        batch.drop_column("quota_refunded")
    op.drop_table("evaluation_judge_pools")
