"""One leased execution per worker, with durable node assignment."""
from alembic import op
import sqlalchemy as sa
from migrations.schema_helpers import ensure_index

revision = "b0479d2e8a61"
down_revision = "a913be2c70dd"
branch_labels = None
depends_on = None


def upgrade():
    columns = {column['name'] for column in sa.inspect(op.get_bind()).get_columns('evaluation_runs')}
    if 'worker_id' not in columns:
        with op.batch_alter_table("evaluation_runs") as batch:
            batch.add_column(sa.Column("worker_id", sa.String(80)))
    ensure_index("ix_evaluation_runs_worker_id", "evaluation_runs", ["worker_id"])
    # Preserve occupied slots during an upgrade with an existing live job.
    bind = op.get_bind()
    workers = bind.execute(sa.text("SELECT id, capabilities FROM evaluation_worker_states")).all()
    import json
    for worker_id, capabilities in workers:
        caps = json.loads(capabilities) if isinstance(capabilities, str) else capabilities
        if caps.get("active_run_id"):
            bind.execute(sa.text("UPDATE evaluation_runs SET worker_id=:worker WHERE id=:run AND status='running'"),
                {"worker": worker_id, "run": caps["active_run_id"]})


def downgrade():
    with op.batch_alter_table("evaluation_runs") as batch:
        batch.drop_index("ix_evaluation_runs_worker_id")
        batch.drop_column("worker_id")
