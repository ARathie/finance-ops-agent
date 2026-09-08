"""The timesheet item: one record per consultant per billing period.

The engagement row in force is snapshotted onto the item when it is created,
so a later edit to the engagement list never changes an invoice already
prepared (docs/decisions.md #3).
"""

from dataclasses import dataclass
from datetime import datetime

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
