"""Snapshot tests for every email the agent sends, and the two leak rules:
the pay rate never appears in anything bound for a client, and the bill rate
never appears in the payment instruction.

Regenerate snapshots with FOPS_UPDATE_SNAPSHOTS=1 uv run pytest tests/unit/test_emails.py
"""

import os
from datetime import date
from pathlib import Path

import pytest

from finance_ops_agent.domain import emails
from finance_ops_agent.domain.emails import (
    EmailAttachment,
    OutgoingEmail,
    SummaryData,
    TimesheetSummary,
)
from finance_ops_agent.domain.invoices import build_invoice
from finance_ops_agent.domain.items import EngagementSnapshot, Item
from finance_ops_agent.domain.money import Hours, Money
from finance_ops_agent.domain.periods import BillingPeriod
from finance_ops_agent.domain.statuses import ItemStatus

SNAPSHOTS = Path(__file__).parent.parent / "fixtures" / "email_snapshots"
ADMIN = "kevin@icon-technologies.com"
AUG = BillingPeriod(date(2026, 8, 1), date(2026, 8, 31))

PAY_RATE_STRINGS = ("100.00", "15,600.00", "pay rate", "owed")
BILL_RATE_STRINGS = ("140.00", "21,840.00", "bill rate")


def worked_example_item() -> Item:
    return Item(
        id=1,
        consultant="Priya Shah",
        client="Acme Corp",
        period=AUG,
        status=ItemStatus.READY,
        snapshot=EngagementSnapshot(
            bill_rate_cents=14_000,
            pay_rate_cents=10_000,
            payment_terms_days=30,
            pay_timing_days=15,
            billing_emails=["ap@acme.example"],
            cc_emails=["jane.doe@acme.example"],
            payee="Priya Shah",
            paid_by="bank transfer",
            engagement_row_number=2,
            role="Senior PeopleSoft Developer",
            client_legal_name="Acme Corporation",
        ),
        approved_hours=Hours(15_600),
        invoice_amount=Money(2_184_000),
        amount_owed=Money(1_560_000),
    )


def summary() -> TimesheetSummary:
    return TimesheetSummary(
        consultant="Priya Shah",
        client="Acme Corp",
        period=AUG,
        total_hours=Hours(15_600),
        approval="approver name date by Jane Doe on 2026-09-01",
        engagement_row=2,
    )


TIMESHEET = EmailAttachment("priya-august.pdf", "a" * 64)
INVOICE_PDF = EmailAttachment("invoice-ICON-2026-1001.pdf", "b" * 64)


def every_email() -> dict[str, OutgoingEmail]:
    item = worked_example_item()
    invoice = build_invoice(item, "ICON-2026-1001", date(2026, 9, 3))
    billing = emails.billing_email(ADMIN, item, invoice, (INVOICE_PDF, TIMESHEET))
    return {
        "timesheet_details": emails.timesheet_details(
            ADMIN, summary(), "everything checks out; I'll prepare the invoice", TIMESHEET
        ),
        "needs_review": emails.needs_review(
            ADMIN,
            "Priya Shah — Acme Corp",
            ["I can't see that the client approved these hours."],
            summary(),
            TIMESHEET,
        ),
        "approve_invoice": emails.approve_invoice(
            ADMIN, item, invoice, billing, (INVOICE_PDF, TIMESHEET)
        ),
        "payment_instruction": emails.payment_instruction(
            ADMIN, item, date(2026, 9, 15), "ICON-2026-1001"
        ),
        "monday_summary": emails.monday_summary(
            ADMIN,
            SummaryData(
                week_of=date(2026, 9, 7),
                timesheets_received=["Priya Shah at Acme Corp"],
                invoices_sent=["ICON-2026-1001 — Acme Corp — $21,840.00"],
                waiting_for_review=["[NO_APPROVAL] I can't see approval."],
                no_timesheet_yet=["Dana Cruz at Acme Corp, Aug 1–31, 2026"],
            ),
            EmailAttachment("tracking.xlsx", "c" * 64),
        ),
        "attention": emails.attention(
            ADMIN,
            "I can't read the mailbox.",
            ["Check that the machine is online.", "The setup steps are in the runbook."],
        ),
        "billing_email": billing,
        "billing_email_replacement": emails.billing_email(
            ADMIN,
            item,
            build_invoice(item, "ICON-2026-1002", date(2026, 9, 5), "ICON-2026-1001"),
            (INVOICE_PDF, TIMESHEET),
        ),
        "dry_run_preview": emails.dry_run_preview(
            ADMIN, item, invoice, billing, (INVOICE_PDF, TIMESHEET)
        ),
    }


def render(email: OutgoingEmail) -> str:
    lines = [
        f"To: {', '.join(email.to)}",
        f"Cc: {', '.join(email.cc)}",
        f"Reply-To: {email.reply_to or ''}",
        f"Subject: {email.subject}",
        f"Attachments: {', '.join(a.filename for a in email.attachments)}",
        "",
        email.body,
        "",
    ]
    return "\n".join(lines)


@pytest.mark.parametrize("name", sorted(every_email()))
def test_email_snapshot(name: str) -> None:
    email = every_email()[name]
    rendered = render(email)
    path = SNAPSHOTS / f"{name}.txt"
    if os.environ.get("FOPS_UPDATE_SNAPSHOTS"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered)
    assert path.exists(), f"missing snapshot {path}; run with FOPS_UPDATE_SNAPSHOTS=1"
    assert rendered == path.read_text(), f"{name} changed; regenerate deliberately"


class TestRateLeaks:
    """Rule 4 of docs/emails.md, and CLAUDE.md rule 1."""

    def test_the_pay_rate_never_appears_in_client_email(self) -> None:
        for name in ("billing_email", "billing_email_replacement"):
            text = render(every_email()[name]).lower()
            for needle in PAY_RATE_STRINGS:
                assert needle not in text, f"{needle!r} leaked into {name}"

    def test_the_bill_rate_never_appears_in_the_payment_instruction(self) -> None:
        text = render(every_email()["payment_instruction"]).lower()
        for needle in BILL_RATE_STRINGS:
            assert needle not in text, f"{needle!r} leaked into the payment instruction"

    def test_the_consultant_is_never_on_the_billing_email(self) -> None:
        email = every_email()["billing_email"]
        recipients = {address.lower() for address in email.to + email.cc}
        assert "priya@example.com" not in recipients
        assert ADMIN in recipients  # Kevin is always on CC

    def test_kevin_is_cc_and_reply_to_on_the_billing_email(self) -> None:
        email = every_email()["billing_email"]
        assert ADMIN in email.cc
        assert email.reply_to == ADMIN
        assert email.to == ("ap@acme.example",)


def test_payload_round_trip() -> None:
    for name, email in every_email().items():
        assert OutgoingEmail.from_payload(email.payload()) == email, name
