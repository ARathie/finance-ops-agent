"""The timesheet item: one record per consultant per billing period.

The engagement row in force is snapshotted onto the item when it is created,
so a later edit to the engagement list never changes an invoice already
prepared (docs/decisions.md #3).
"""

from dataclasses import dataclass
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from finance_ops_agent.domain.money import Hours, Money
from finance_ops_agent.domain.periods import BillingPeriod
from finance_ops_agent.domain.statuses import ItemStatus


class EngagementSnapshot(BaseModel):
    """The engagement row the item was matched to: rates, terms, contacts, row number."""

    model_config = ConfigDict(frozen=True)

    bill_rate_cents: int
    pay_rate_cents: int
    payment_terms_days: int
    pay_timing_days: int
    billing_emails: list[str]
    cc_emails: list[str]
    payee: str  # the consultant, or their vendor company
    paid_by: str
    engagement_row_number: int
    role: str = ""  # printed on the invoice line
    client_legal_name: str = ""  # printed on the invoice
    quickbooks_customer: str = ""  # the Customers name in QuickBooks, if different
    client_delivery: str = "email"  # email, or portal (Kevin uploads it himself)
    send_automatically: bool = False  # the engagement row's "Send automatically" column


@dataclass(frozen=True)
class Item:
    id: int
    consultant: str
    client: str
    period: BillingPeriod
    status: ItemStatus
    snapshot: EngagementSnapshot
    approved_hours: Hours | None = None
    invoice_amount: Money | None = None
    amount_owed: Money | None = None


@dataclass(frozen=True)
class AuditEntry:
    """One line of the append-only history: when, what, which item, and the details."""

    at: datetime
    what: str
    item_id: int | None
    details: dict[str, object]


@dataclass(frozen=True)
class TimesheetRecord:
    """One timesheet file processed against an item."""

    item_id: int
    sha256: str
    reading: dict[str, object]  # the TimesheetReading, as stored JSON
    model: str
    prompt_version: str
    is_duplicate: bool
    is_correction: bool


@dataclass(frozen=True)
class ReviewRecord:
    """One review reason raised for Kevin, open until he answers."""

    id: int
    item_id: int | None
    code: str
    message: str
    status: str  # open / answered / ignored


@dataclass(frozen=True)
class OutgoingRecord:
    """An email or accounting write, written down before it happens."""

    kind: str
    idempotency_key: str
    item_id: int | None
    payload: dict[str, object]
    status: str  # pending / in_flight / done / failed
    draft_id: str | None = None
    attempts: int = 0
    last_error: str | None = None


@dataclass(frozen=True)
class InvoiceRecord:
    """One invoice per item (plus replacements after corrections)."""

    id: int
    item_id: int
    number: str
    external_id: str
    amount_cents: int
    issue_date: date
    due_date: date
    pdf_sha256: str | None
    status: str  # created / sent / paid / cancelled
    replaces_number: str | None = None


@dataclass(frozen=True)
class PaymentInstructionRecord:
    """What Kevin was told a consultant or vendor is owed, once per item."""

    item_id: int
    payee: str
    amount_cents: int
    due_date: date
    method: str
