"""Messages are keyed by their Message-ID; outgoing emails record the send.

The earlier mailbox adapter stored a provider id, an internet message id, and
a conversation id. With IMAP the Message-ID header is the key, replies thread
by In-Reply-To and References, and an outgoing email carries the Message-ID
the agent made before sending, when the send started, and when the server
accepted it (docs/integrations/email-imap-smtp.md).

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-09
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Whatever the old provider id was, the internet message id was the real
    # Message-ID header; carry it over before the column becomes the key.
    op.execute(
        "UPDATE messages SET provider_id = internet_message_id"
        " WHERE internet_message_id IS NOT NULL AND internet_message_id != ''"
    )
    with op.batch_alter_table("messages") as batch:
        batch.alter_column("provider_id", new_column_name="message_id")
        batch.add_column(sa.Column("in_reply_to", sa.String(), nullable=True))
        batch.add_column(sa.Column("references_ids", sa.String(), nullable=True))
        batch.drop_column("internet_message_id")
        batch.drop_column("conversation_id")
    with op.batch_alter_table("outgoing") as batch:
        batch.alter_column("draft_id", new_column_name="message_id")
        batch.add_column(sa.Column("started_at", sa.String(), nullable=True))
        batch.add_column(sa.Column("accepted_at", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("outgoing") as batch:
        batch.drop_column("accepted_at")
        batch.drop_column("started_at")
        batch.alter_column("message_id", new_column_name="draft_id")
    with op.batch_alter_table("messages") as batch:
        batch.add_column(sa.Column("conversation_id", sa.String(), nullable=True))
        batch.add_column(sa.Column("internet_message_id", sa.String(), nullable=True))
        batch.drop_column("references_ids")
        batch.drop_column("in_reply_to")
        batch.alter_column("message_id", new_column_name="provider_id")
    op.execute("UPDATE messages SET internet_message_id = provider_id")
