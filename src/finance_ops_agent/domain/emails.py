"""Every email the agent sends, composed exactly as docs/emails.md describes.

These are pure functions from domain objects to an OutgoingEmail; nothing here
sends anything. Two rules are load-bearing and tested: the pay rate never
appears in anything bound for a client, and the bill rate never appears in the
payment instruction. Kevin-facing wording uses the glossary's plain words.
"""

from dataclasses import dataclass, field
from datetime import date

from finance_ops_agent.domain.invoices import Invoice
from finance_ops_agent.domain.items import Item
from finance_ops_agent.domain.money import Hours, Money
from finance_ops_agent.domain.periods import BillingPeriod

_MONTHS = [
    "",
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
]


def format_period(period: BillingPeriod) -> str:
    """Aug 1–31, 2026 — or Aug 25–Sep 7, 2026 when it crosses a month."""
    start, end = period.start, period.end
    if (start.year, start.month) == (end.year, end.month):
        return f"{_MONTHS[start.month]} {start.day}–{end.day}, {end.year}"
    return f"{_MONTHS[start.month]} {start.day}–{_MONTHS[end.month]} {end.day}, {end.year}"


def format_month(period: BillingPeriod) -> str:
    return f"{_MONTHS[period.end.month]} {period.end.year}"


def dollars(amount: Money) -> str:
    return f"${amount}"


@dataclass(frozen=True)
class EmailAttachment:
    filename: str
    sha256: str  # the content lives in the file store


@dataclass(frozen=True)
class OutgoingEmail:
    to: tuple[str, ...]
    subject: str
    body: str
    cc: tuple[str, ...] = ()
    reply_to: str | None = None
    attachments: tuple[EmailAttachment, ...] = ()

    def payload(self) -> dict[str, object]:
        return {
            "to": list(self.to),
            "cc": list(self.cc),
            "reply_to": self.reply_to,
            "subject": self.subject,
            "body": self.body,
            "attachments": [[a.filename, a.sha256] for a in self.attachments],
        }

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "OutgoingEmail":
        raw_attachments = payload.get("attachments") or []
        assert isinstance(raw_attachments, list)
        to, cc = payload["to"], payload["cc"]
        assert isinstance(to, list) and isinstance(cc, list)
        reply_to = payload.get("reply_to")
        return cls(
            to=tuple(str(address) for address in to),
            cc=tuple(str(address) for address in cc),
            reply_to=None if reply_to is None else str(reply_to),
            subject=str(payload["subject"]),
            body=str(payload["body"]),
            attachments=tuple(
                EmailAttachment(str(entry[0]), str(entry[1])) for entry in raw_attachments
            ),
        )


@dataclass(frozen=True)
class TimesheetSummary:
    """What the agent read, shown the same way in several emails."""

    consultant: str
    client: str
    period: BillingPeriod | None
    total_hours: Hours | None
    approval: str  # e.g. "Approved by Jane Doe on 2026-09-01", or "no approval found"
    engagement_row: int | None = None


def _period_text(period: BillingPeriod | None) -> str:
    return "unclear" if period is None else format_period(period)


def _summary_lines(summary: TimesheetSummary) -> list[str]:
    hours = "not found" if summary.total_hours is None else f"{summary.total_hours} hours"
    lines = [
        f"Consultant: {summary.consultant}",
        f"Client: {summary.client}",
        f"Period: {_period_text(summary.period)}",
        f"Hours: {hours}",
        f"Approval: {summary.approval}",
    ]
    if summary.engagement_row is not None:
        lines.append(f"Engagement list row: {summary.engagement_row}")
    return lines


# 1. Timesheet details for your records


def timesheet_details(
    admin: str,
    summary: TimesheetSummary,
    next_step: str,
    timesheet: EmailAttachment | None,
) -> OutgoingEmail:
    hours = "" if summary.total_hours is None else f" — {summary.total_hours} hours"
    return OutgoingEmail(
        to=(admin,),
        subject=(
            f"Timesheet received: {summary.consultant} — {summary.client}"
            f" — {_period_text(summary.period)}{hours}"
        ),
        body="\n".join(
            [
                "A timesheet arrived. Here is what I read, for your records.",
                "",
                *_summary_lines(summary),
                "",
                f"What happens next: {next_step}",
            ]
        ),
        attachments=(timesheet,) if timesheet else (),
    )


# 2. Needs your review


def needs_review(
    admin: str,
    about: str,  # "Priya Shah — Acme Corp — Aug 2026", or a plain description
    problems: list[str],
    summary: TimesheetSummary | None = None,
    timesheet: EmailAttachment | None = None,
    asking_again: bool = False,
) -> OutgoingEmail:
    headline = problems[0].rstrip(".") if len(problems) == 1 else "several things to check"
    body_lines = [
        "I need your help with this one."
        if not asking_again
        else "Sorry to ask again — I still can't tell from your last reply."
    ]
    if summary is not None:
        body_lines += ["", "What I read:", *_summary_lines(summary)]
    body_lines += ["", "What needs your review:"]
    body_lines += [f"- {problem}" for problem in problems]
    body_lines += [
        "",
        "You can fix or add the row in the engagement list, or just reply to this",
        'email with the answer, or reply "ignore".',
        'Replies that work: "this is for Acme", "use 152 hours",',
        '"approved by Jane Doe on 9/3", "use the new one", "ignore".',
    ]
    return OutgoingEmail(
        to=(admin,),
        subject=f"Needs your review: {about} — {headline}",
        body="\n".join(body_lines),
        attachments=(timesheet,) if timesheet else (),
    )


# 3. Approve this invoice?


def approve_invoice(
    admin: str,
    item: Item,
    invoice: Invoice,
    billing_preview: "OutgoingEmail",
    attachments: tuple[EmailAttachment, ...],
) -> OutgoingEmail:
    assert item.approved_hours is not None and item.invoice_amount is not None
    return OutgoingEmail(
        to=(admin,),
        subject=(
            f"Approve? Invoice for {item.client} — {item.consultant}"
            f" — {format_month(item.period)} — {dollars(invoice.total)}"
        ),
        body="\n".join(
            [
                'Everything checks out. Reply "approve" to send, or "cancel" to stop.',
                "",
                f"Client: {item.client}",
                f"Billing email: {', '.join(item.snapshot.billing_emails)}",
                f"Consultant: {item.consultant}",
                f"Period: {format_period(item.period)}",
                f"Approved hours: {item.approved_hours}",
                f"Bill rate: {dollars(Money(item.snapshot.bill_rate_cents))}",
                f"Total: {dollars(invoice.total)}",
                f"Due: {invoice.due_date}",
                "",
                "The billing email, exactly as it will be sent:",
                "",
                f"Subject: {billing_preview.subject}",
                "",
                billing_preview.body,
            ]
        ),
        attachments=attachments,
    )


# 4. Payment instruction


def payment_instruction(
    admin: str, item: Item, due: date, invoice_number: str | None
) -> OutgoingEmail:
    assert item.approved_hours is not None and item.amount_owed is not None
    snapshot = item.snapshot
    relates = (
        f"This relates to invoice {invoice_number}."
        if invoice_number
        else "The invoice for this period has not been sent yet (dry run)."
    )
    return OutgoingEmail(
        to=(admin,),
        subject=(
            f"Payment due {due}: {snapshot.payee} — {format_month(item.period)}"
            f" — {dollars(item.amount_owed)}"
        ),
        body="\n".join(
            [
                f"Who to pay: {snapshot.payee} ({snapshot.paid_by})",
                f"For: {item.consultant} at {item.client}, {format_period(item.period)}",
                f"Approved hours: {item.approved_hours}",
                f"Pay rate: {dollars(Money(snapshot.pay_rate_cents))}",
                f"Amount owed: {dollars(item.amount_owed)}",
                f"Due: {due}",
                "",
                relates,
                "I don't follow up on payments; this stays with you, as today.",
            ]
        ),
    )


# 5. Monday summary


@dataclass(frozen=True)
class SummaryData:
    week_of: date
    timesheets_received: list[str] = field(default_factory=list)
    invoices_sent: list[str] = field(default_factory=list)
    waiting_for_review: list[str] = field(default_factory=list)
    waiting_for_approval: list[str] = field(default_factory=list)
    no_timesheet_yet: list[str] = field(default_factory=list)
    set_aside: list[str] = field(default_factory=list)
    duplicates_filed: list[str] = field(default_factory=list)


def monday_summary(
    admin: str, data: SummaryData, tracking_sheet: EmailAttachment | None
) -> OutgoingEmail:
    def section(title: str, entries: list[str]) -> list[str]:
        if not entries:
            return [f"{title}: none", ""]
        return [f"{title}:", *[f"- {entry}" for entry in entries], ""]

    body_lines = [
        *section("Timesheets received last week", data.timesheets_received),
        *section("Invoices sent", data.invoices_sent),
        *section("Waiting for your review", data.waiting_for_review),
        *section("Waiting for your approval", data.waiting_for_approval),
        *section("No timesheet yet for the last period", data.no_timesheet_yet),
        *section("Emails set aside (unknown senders)", data.set_aside),
        *section("Duplicates filed", data.duplicates_filed),
    ]
    return OutgoingEmail(
        to=(admin,),
        subject=(
            f"Weekly summary — week of {_MONTHS[data.week_of.month]}"
            f" {data.week_of.day}, {data.week_of.year}"
        ),
        body="\n".join(body_lines).rstrip(),
        attachments=(tracking_sheet,) if tracking_sheet else (),
    )


# 6. Something needs attention


def attention(admin: str, problem: str, steps: list[str]) -> OutgoingEmail:
    return OutgoingEmail(
        to=(admin,),
        subject=f"Something needs attention: {problem}",
        body="\n".join([problem, "", "What to do:", *[f"- {step}" for step in steps]]),
    )


# 7. The billing email (the only one that goes to a client)


def billing_email(
    admin: str,
    item: Item,
    invoice: Invoice,
    attachments: tuple[EmailAttachment, ...],
) -> OutgoingEmail:
    """To the client's billing email(s), Kevin always on CC, Reply-To Kevin.

    Never mentions the pay rate, and the consultant is never on it."""
    assert item.approved_hours is not None
    replaces = (
        [f"This invoice replaces invoice {invoice.replaces_number}, which has been cancelled.", ""]
        if invoice.replaces_number
        else []
    )
    body = "\n".join(
        [
            "Hello,",
            "",
            *replaces,
            f"Please find attached Icon Technologies invoice {invoice.number} for"
            f" {item.consultant}'s services for {format_period(item.period)}"
            f" ({item.approved_hours} approved hours), along with the approved"
            " timesheet."
            f" Payment terms are {item.snapshot.payment_terms_days} days; the invoice"
            f" is due {invoice.due_date}.",
            "",
            "Please let us know if you have any questions.",
            "",
            "Thank you,",
            "Icon Technologies",
        ]
    )
    return OutgoingEmail(
        to=tuple(item.snapshot.billing_emails),
        cc=(admin, *item.snapshot.cc_emails),
        reply_to=admin,
        subject=(
            f"Icon Technologies invoice {invoice.number} — {item.consultant}"
            f" — {format_month(item.period)}"
        ),
        body=body,
        attachments=attachments,
    )


# Dry run: what the agent would send, shown to Kevin instead


def dry_run_preview(
    admin: str,
    item: Item,
    invoice: Invoice,
    billing: OutgoingEmail,
    attachments: tuple[EmailAttachment, ...],
) -> OutgoingEmail:
    return OutgoingEmail(
        to=(admin,),
        subject=(
            f"Dry run — would invoice {item.client} — {item.consultant}"
            f" — {format_month(item.period)} — {dollars(invoice.total)}"
        ),
        body="\n".join(
            [
                "Dry run: nothing was sent to the client and no invoice was created.",
                "Here is exactly what I would send.",
                "",
                f"To: {', '.join(billing.to)}",
                f"CC: {', '.join(billing.cc)}",
                f"Subject: {billing.subject}",
                "",
                billing.body,
            ]
        ),
        attachments=attachments,
    )
