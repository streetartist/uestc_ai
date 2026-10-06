"""Separate private team trials from formal submissions; preserve legacy runs."""
from alembic import op
import sqlalchemy as sa

revision = "e7a19b4c0832"
down_revision = "d1e6f8a92410"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("evaluation_runs", sa.Column("purpose", sa.String(24), nullable=False, server_default="submission"))
    op.add_column("evaluation_runs", sa.Column("package_storage_name", sa.String(255), nullable=True))


def downgrade():
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT COUNT(*) FROM evaluation_runs WHERE purpose = 'trial'")).scalar():
        raise SystemExit("Cannot downgrade while independent evaluation trials exist")
    op.drop_column("evaluation_runs", "package_storage_name")
    op.drop_column("evaluation_runs", "purpose")
