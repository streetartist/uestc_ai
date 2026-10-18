"""Team-owned immutable model weight assets."""
from alembic import op
import sqlalchemy as sa
from migrations.schema_helpers import ensure_index, ensure_table

revision = 'f18c20261018'
down_revision = 'e16a20261016'
branch_labels = None
depends_on = None


def upgrade():
    ensure_table('model_weight_assets',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('team_id', sa.String(36), sa.ForeignKey('teams.id', ondelete='CASCADE'), nullable=False),
        sa.Column('problem_id', sa.String(36), sa.ForeignKey('problems.id', ondelete='CASCADE'), nullable=False),
        sa.Column('original_name', sa.String(255), nullable=False),
        sa.Column('storage_name', sa.String(255), unique=True, nullable=False),
        sa.Column('size', sa.BigInteger(), nullable=False),
        sa.Column('sha256', sa.String(64), nullable=False),
        sa.Column('state', sa.String(16), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    ensure_index('ix_model_weight_assets_team_id', 'model_weight_assets', ['team_id'])
    ensure_index('ix_model_weight_assets_problem_id', 'model_weight_assets', ['problem_id'])


def downgrade():
    op.drop_table('model_weight_assets')
