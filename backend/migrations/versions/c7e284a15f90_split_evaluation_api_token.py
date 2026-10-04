"""Separate the contestant API quota token from the worker lease.

Revision ID: c7e284a15f90
Revises: b6d92ea71c03
"""
from alembic import op
import sqlalchemy as sa


revision = "c7e284a15f90"
down_revision = "b6d92ea71c03"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("evaluation_runs") as batch_op:
        batch_op.add_column(sa.Column("api_token_hash", sa.String(64)))


def downgrade():
    with op.batch_alter_table("evaluation_runs") as batch_op:
        batch_op.drop_column("api_token_hash")
