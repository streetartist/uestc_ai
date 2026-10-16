"""Collect campus student numbers and explicitly approve post-competition showcases."""
from alembic import op
import sqlalchemy as sa

revision = "e16a20261016"
down_revision = "d5b8e3a91c47"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if "student_number" not in {column["name"] for column in inspector.get_columns("users")}:
        op.add_column("users", sa.Column("student_number", sa.String(32), nullable=True))
    if "publication" not in {column["name"] for column in inspector.get_columns("submissions")}:
        op.add_column("submissions", sa.Column("publication", sa.JSON(), nullable=False, server_default="{}"))


def downgrade():
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT 1 FROM users WHERE student_number IS NOT NULL LIMIT 1")).first():
        raise RuntimeError("Cannot remove collected student numbers; export the data before downgrade")
    if bind.execute(sa.text("SELECT 1 FROM submissions WHERE CAST(publication AS TEXT) NOT IN ('{}', 'null') LIMIT 1")).first():
        raise RuntimeError("Cannot remove saved showcase approvals; export the data before downgrade")
    with op.batch_alter_table("submissions") as batch:
        batch.drop_column("publication")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("student_number")
