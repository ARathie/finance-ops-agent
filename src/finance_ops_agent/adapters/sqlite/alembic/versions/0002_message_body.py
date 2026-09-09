"""Store the email body so Kevin's replies can be read after a restart.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-09
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("messages") as batch:
        batch.add_column(sa.Column("body_text", sa.String(), nullable=False, server_default=""))


def downgrade() -> None:
    with op.batch_alter_table("messages") as batch:
        batch.drop_column("body_text")
