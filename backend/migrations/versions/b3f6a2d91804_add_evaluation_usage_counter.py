"""Persist per-team test usage independently of submission overwrites.

Revision ID: b3f6a2d91804
Revises: a2c5e94d320b
"""
from alembic import op
import sqlalchemy as sa

revision = "b3f6a2d91804"
down_revision = "a2c5e94d320b"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("submissions") as batch:
        batch.add_column(sa.Column("evaluation_runs_used", sa.Integer(), nullable=False, server_default="0"))
    # Count all statuses, including failed and superseded historical tests.
    op.execute(sa.text("""UPDATE submissions SET evaluation_runs_used = (
        SELECT COUNT(*) FROM evaluation_runs r
        JOIN submission_versions v ON v.id = r.submission_version_id
        WHERE v.submission_id = submissions.id
    )"""))


def downgrade():
    if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM submissions WHERE evaluation_runs_used > 0")).scalar():
        raise RuntimeError("Cannot discard evaluation quota usage history")
    with op.batch_alter_table("submissions") as batch:
        batch.drop_column("evaluation_runs_used")
