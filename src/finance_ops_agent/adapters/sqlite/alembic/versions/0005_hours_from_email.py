"""A timesheet record can be hours a salaried employee wrote in the email itself.

There is no file then (decision 69): the email's text is kept as the record's
file, and two columns say so. `from_email_text` keeps that text off the
client's billing email, which only ever carries a real timesheet. `message_id`
links the record to its email, which an attachment's own row does for every
other record.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-29
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("timesheets") as batch:
        batch.add_column(
            sa.Column("from_email_text", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(sa.Column("message_id", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("timesheets") as batch:
        batch.drop_column("message_id")
        batch.drop_column("from_email_text")
