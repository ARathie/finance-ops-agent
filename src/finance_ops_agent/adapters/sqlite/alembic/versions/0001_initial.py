"""All the tables from docs/technical-design.md.

Revision ID: 0001
Revises:
Create Date: 2026-09-08
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider_id", sa.String(), nullable=False, unique=True),
        sa.Column("internet_message_id", sa.String(), nullable=True),
        sa.Column("conversation_id", sa.String(), nullable=True),
        sa.Column("from_address", sa.String(), nullable=False),
        sa.Column("to_addresses", sa.String(), nullable=False),
        sa.Column("subject", sa.String(), nullable=False),
        sa.Column("received_at", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("processed_at", sa.String(), nullable=True),
    )
    op.create_table(
        "attachments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("message_id", sa.Integer(), sa.ForeignKey("messages.id"), nullable=False),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("mime_type", sa.String(), nullable=False),
        sa.Column("sha256", sa.String(), nullable=False, index=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
    )
    op.create_table(
        "items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("consultant", sa.String(), nullable=False),
        sa.Column("client", sa.String(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("engagement_snapshot", sa.JSON(), nullable=False),
        sa.Column("approved_hours_hundredths", sa.Integer(), nullable=True),
        sa.Column("invoice_amount_cents", sa.Integer(), nullable=True),
        sa.Column("amount_owed_cents", sa.Integer(), nullable=True),
        sa.UniqueConstraint(
            "consultant", "client", "period_start", "period_end", name="uq_item_period"
        ),
    )
    op.create_table(
        "timesheets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("item_id", sa.Integer(), sa.ForeignKey("items.id"), nullable=False),
        sa.Column("attachment_sha256", sa.String(), nullable=False),
        sa.Column("reading", sa.JSON(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("prompt_version", sa.String(), nullable=False),
        sa.Column("is_duplicate", sa.Boolean(), nullable=False),
        sa.Column("is_correction", sa.Boolean(), nullable=False),
    )
    op.create_table(
        "review_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("item_id", sa.Integer(), sa.ForeignKey("items.id"), nullable=True),
        sa.Column("message_id", sa.Integer(), sa.ForeignKey("messages.id"), nullable=True),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("message", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("answer", sa.JSON(), nullable=True),
        sa.Column("answered_at", sa.String(), nullable=True),
    )
    op.create_table(
        "invoices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("item_id", sa.Integer(), sa.ForeignKey("items.id"), nullable=False),
        sa.Column("number", sa.String(), nullable=False),
        sa.Column("external_id", sa.String(), nullable=True),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("issue_date", sa.Date(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("pdf_sha256", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("replaces_invoice_id", sa.Integer(), sa.ForeignKey("invoices.id"), nullable=True),
    )
    op.create_table(
        "payment_instructions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("item_id", sa.Integer(), sa.ForeignKey("items.id"), nullable=False),
        sa.Column("payee", sa.String(), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("method", sa.String(), nullable=False),
        sa.Column("emailed_at", sa.String(), nullable=True),
    )
    op.create_table(
        "outgoing",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False, unique=True),
        sa.Column("item_id", sa.Integer(), sa.ForeignKey("items.id"), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("draft_id", sa.String(), nullable=True),
        sa.Column("external_id", sa.String(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.String(), nullable=True),
    )
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("at", sa.String(), nullable=False),
        sa.Column("what", sa.String(), nullable=False),
        sa.Column("item_id", sa.Integer(), sa.ForeignKey("items.id"), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
    )
    op.create_table(
        "state",
        sa.Column("key", sa.String(), primary_key=True),
        sa.Column("value", sa.String(), nullable=False),
    )


def downgrade() -> None:
    for table in (
        "state",
        "audit_log",
        "outgoing",
        "payment_instructions",
        "invoices",
        "review_items",
        "timesheets",
        "items",
        "attachments",
        "messages",
    ):
        op.drop_table(table)
