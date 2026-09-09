"""The tables, exactly as listed in docs/technical-design.md.

Timestamps (`*_at` columns) are ISO-8601 UTC strings. JSON columns hold
Pydantic-validated dicts. Money is INTEGER cents; hours INTEGER hundredths.
"""

from datetime import date

from sqlalchemy import JSON, Date, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, object]: JSON}


class MessageRow(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[str] = mapped_column(String, unique=True)
    in_reply_to: Mapped[str | None]
    references_ids: Mapped[str | None]  # space-separated Message-IDs
    from_address: Mapped[str]
    to_addresses: Mapped[str]
    subject: Mapped[str]
    body_text: Mapped[str] = mapped_column(String, default="")
    received_at: Mapped[str]
    kind: Mapped[str]
    processed_at: Mapped[str | None]


class AttachmentRow(Base):
    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id"))
    filename: Mapped[str]
    mime_type: Mapped[str]
    sha256: Mapped[str] = mapped_column(String, index=True)
    size_bytes: Mapped[int]


class ItemRow(Base):
    __tablename__ = "items"
    __table_args__ = (
        UniqueConstraint(
            "consultant", "client", "period_start", "period_end", name="uq_item_period"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    consultant: Mapped[str]
    client: Mapped[str]
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    status: Mapped[str]
    engagement_snapshot: Mapped[dict[str, object]]
    approved_hours_hundredths: Mapped[int | None] = mapped_column(Integer)
    invoice_amount_cents: Mapped[int | None] = mapped_column(Integer)
    amount_owed_cents: Mapped[int | None] = mapped_column(Integer)


class TimesheetRow(Base):
    __tablename__ = "timesheets"

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    attachment_sha256: Mapped[str]
    reading: Mapped[dict[str, object]]
    model: Mapped[str]
    prompt_version: Mapped[str]
    is_duplicate: Mapped[bool]
    is_correction: Mapped[bool]


class ReviewItemRow(Base):
    __tablename__ = "review_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int | None] = mapped_column(ForeignKey("items.id"))
    message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id"))
    code: Mapped[str]
    message: Mapped[str]
    status: Mapped[str]  # open / answered / ignored
    answer: Mapped[dict[str, object] | None] = mapped_column(JSON)
    answered_at: Mapped[str | None]


class InvoiceRow(Base):
    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    number: Mapped[str]
    external_id: Mapped[str | None]
    amount_cents: Mapped[int]
    issue_date: Mapped[date] = mapped_column(Date)
    due_date: Mapped[date] = mapped_column(Date)
    pdf_sha256: Mapped[str | None]
    status: Mapped[str]  # created / sent / paid / cancelled
    replaces_invoice_id: Mapped[int | None] = mapped_column(ForeignKey("invoices.id"))


class PaymentInstructionRow(Base):
    __tablename__ = "payment_instructions"

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    payee: Mapped[str]
    amount_cents: Mapped[int]
    due_date: Mapped[date] = mapped_column(Date)
    method: Mapped[str]
    emailed_at: Mapped[str | None]


class OutgoingRow(Base):
    __tablename__ = "outgoing"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str]
    idempotency_key: Mapped[str] = mapped_column(String, unique=True)
    item_id: Mapped[int | None] = mapped_column(ForeignKey("items.id"))
    payload: Mapped[dict[str, object]]
    status: Mapped[str]  # pending / in_flight / done / failed
    message_id: Mapped[str | None]
    started_at: Mapped[str | None]
    accepted_at: Mapped[str | None]
    external_id: Mapped[str | None]
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None]


class AuditLogRow(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[str]
    what: Mapped[str]
    item_id: Mapped[int | None] = mapped_column(ForeignKey("items.id"))
    details: Mapped[dict[str, object]]


class StateRow(Base):
    __tablename__ = "state"

    key: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[str]
