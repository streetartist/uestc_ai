"""Private contribution attachments and editorial review feedback."""
from alembic import op
import sqlalchemy as sa

revision = "d1e6f8a92410"
down_revision = "c4d7e2a91065"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("markdown_assets", sa.Column("visibility", sa.String(24), nullable=False, server_default="public"))
    op.add_column("content", sa.Column("review_note", sa.Text(), nullable=False, server_default=""))


def downgrade():
    op.drop_column("content", "review_note")
    op.drop_column("markdown_assets", "visibility")
