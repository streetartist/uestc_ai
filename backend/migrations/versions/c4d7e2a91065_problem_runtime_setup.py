"""Private problem runtimes and frozen evaluation snapshots."""
from alembic import op
import sqlalchemy as sa

revision = "c4d7e2a91065"
down_revision = "b3f6a2d91804"
branch_labels = None
depends_on = None


def upgrade():
    # Dev AUTO_CREATE_SCHEMA may already have created new tables on reload.
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("problem_runtimes"):
        op.create_table("problem_runtimes",
        sa.Column("problem_id", sa.String(36), sa.ForeignKey("problems.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    if not inspector.has_table("evaluation_worker_states"):
        op.create_table("evaluation_worker_states",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False))
    if "runtime_snapshot" not in {column["name"] for column in inspector.get_columns("evaluation_runs")}:
        with op.batch_alter_table("evaluation_runs") as batch:
            batch.add_column(sa.Column("runtime_snapshot", sa.JSON(none_as_null=True), nullable=True))


def downgrade():
    if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM problem_runtimes")).scalar():
        raise RuntimeError("Export private problem runtimes before downgrade")
    if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM evaluation_runs WHERE runtime_snapshot IS NOT NULL AND CAST(runtime_snapshot AS TEXT) != 'null'")).scalar():
        raise RuntimeError("Cannot discard frozen evaluation runtimes")
    with op.batch_alter_table("evaluation_runs") as batch:
        batch.drop_column("runtime_snapshot")
    op.drop_table("evaluation_worker_states")
    op.drop_table("problem_runtimes")
