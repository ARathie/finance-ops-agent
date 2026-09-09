"""The invoice: one line, consultant — role — period, hours x bill rate.

Amounts are computed once, from the item's snapshot, and printed from there
ever after. The pay rate never appears anywhere near an invoice.
"""

from dataclasses import dataclass
from datetime import date, timedelta

from finance_ops_agent.domain.items import Item
from finance_ops_agent.domain.money import Hours, Money
from finance_ops_agent.domain.periods import BillingPeriod

DRAFT_NUMBER = "(assigned on approval)"


@dataclass(frozen=True)
class Invoice:
    number: str
    client_legal_name: str
    consultant: str
    role: str
    period: BillingPeriod
    approved_hours: Hours
    bill_rate: Money
    total: Money
    issue_date: date
    due_date: date
    replaces_number: str | None = None
    quickbooks_customer: str = ""  # blank means "the same as the legal name"

    def line_description(self) -> str:
        role = f" — {self.role}" if self.role else ""
        return f"{self.consultant}{role} — {self.period.start} to {self.period.end}"


def build_invoice(
    item: Item, number: str, issue_date: date, replaces_number: str | None = None
) -> Invoice:
    """The invoice for a ready item. Hours and amounts come off the item, where
    they were computed once; the due date follows the client's payment terms."""
    if item.approved_hours is None or item.invoice_amount is None:
        raise ValueError(f"item {item.id} has no approved hours or amount yet")
    return Invoice(
        number=number,
        client_legal_name=item.snapshot.client_legal_name or item.client,
        consultant=item.consultant,
        role=item.snapshot.role,
        period=item.period,
        approved_hours=item.approved_hours,
        bill_rate=Money(item.snapshot.bill_rate_cents),
        total=item.invoice_amount,
        issue_date=issue_date,
        due_date=issue_date + timedelta(days=item.snapshot.payment_terms_days),
        replaces_number=replaces_number,
        quickbooks_customer=item.snapshot.quickbooks_customer,
    )
