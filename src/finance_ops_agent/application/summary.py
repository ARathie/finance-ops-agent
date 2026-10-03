"""The Monday summary and the tracking sheet, assembled from the store."""

import json
from datetime import date, timedelta

from finance_ops_agent.application.context import RunDeps, RunReport
from finance_ops_agent.application.diagnosis import diagnose_everything, looking_at
from finance_ops_agent.application.outgoing import enqueue_email
from finance_ops_agent.domain import emails
from finance_ops_agent.domain.emails import EmailAttachment, SummaryData, format_period
from finance_ops_agent.domain.money import Money
from finance_ops_agent.domain.statuses import ItemStatus
from finance_ops_agent.domain.tracking import TrackingRow
from finance_ops_agent.ports.accounting import AccountingFailed

LAST_SUMMARY_KEY = "last_summary"
MISSING_TIMESHEET_GRACE_DAYS = 7
# Duplicates filed quietly, with the day each was filed, so the Monday summary
# can list last week's (decision 64). An exact copy of a file leaves nothing
# else behind: it is never attached to an item.
DUPLICATES_FILED_KEY = "duplicates_filed"
_DUPLICATES_KEPT_DAYS = 14


def note_duplicate_filed(deps: RunDeps, what: str) -> None:
    """Write down one duplicate filed quietly, for the next Monday summary.
    Entries older than two weeks are dropped as each new one is added."""
    today = deps.clock.today()
    kept = [
        entry
        for entry in _duplicates_filed(deps)
        if date.fromisoformat(entry[0]) >= today - timedelta(days=_DUPLICATES_KEPT_DAYS)
    ]
    kept.append((today.isoformat(), what))
    deps.store.set_state(DUPLICATES_FILED_KEY, json.dumps(kept))


def _duplicates_filed(deps: RunDeps) -> list[tuple[str, str]]:
    text = deps.store.get_state(DUPLICATES_FILED_KEY)
    if not text:
        return []
    return [(str(on), str(what)) for on, what in json.loads(text)]


def tracking_rows(deps: RunDeps) -> list[TrackingRow]:
    rows: list[TrackingRow] = []
    for item in deps.store.list_items():
        invoices = [
            record
            for record in deps.store.invoices_for_item(item.id)
            if record.status != "cancelled"
        ]
        invoice = invoices[-1] if invoices else None
        instructions = deps.store.payment_instructions_for_item(item.id)
        owed_to = ""
        if instructions:
            instruction = instructions[-1]
            owed_to = f"{instruction.payee} ({instruction.method}), due {instruction.due_date}"
        open_codes = sorted(
            {review.code for review in deps.store.open_reviews() if review.item_id == item.id}
        )
        received = ""
        for entry in deps.store.audit_entries(item.id):
            if entry.what == "item created" and entry.details.get("status") == "received":
                received = str(entry.at.date())
            if entry.what == "status changed" and entry.details.get("to") == "received":
                received = received or str(entry.at.date())
        sent = ""
        for entry in deps.store.audit_entries(item.id):
            if entry.what == "status changed" and entry.details.get("to") == "invoice_sent":
                sent = str(entry.at.date())
        rows.append(
            TrackingRow(
                consultant=item.consultant,
                client=item.client,
                period=f"{item.period.start} to {item.period.end}",
                approved_hours="" if item.approved_hours is None else str(item.approved_hours),
                bill_rate=str(Money(item.snapshot.bill_rate_cents)),
                invoice_amount=("" if item.invoice_amount is None else str(item.invoice_amount)),
                pay_rate=str(Money(item.snapshot.pay_rate_cents)),
                amount_owed="" if item.amount_owed is None else str(item.amount_owed),
                owed_to=owed_to,
                status=item.status.value,
                timesheet_received=received,
                invoice_number="" if invoice is None else invoice.number,
                invoice_sent=sent,
                client_paid="" if invoice is None or invoice.status != "paid" else "yes",
                notes="needs review: " + ", ".join(open_codes) if open_codes else "",
            )
        )
    return rows


def enqueue_monday_summary(deps: RunDeps, report: RunReport, tracking_sha: str | None) -> None:
    today = deps.clock.today()
    if today.weekday() != 0:  # Monday
        return
    if deps.store.get_state(LAST_SUMMARY_KEY) == today.isoformat():
        return
    week_ago = today - timedelta(days=7)
    items = deps.store.list_items()
    received: list[str] = []
    for item in items:
        for entry in deps.store.audit_entries(item.id):
            arrived = entry.what == "item created" and entry.details.get("status") == "received"
            arrived = arrived or (
                entry.what == "status changed" and entry.details.get("to") == "received"
            )
            if arrived and entry.at.date() >= week_ago:
                received.append(f"{item.consultant} at {item.client}")
                break
    invoices_sent = []
    for item in items:
        for record in deps.store.invoices_for_item(item.id):
            if record.status in ("sent", "paid") and record.issue_date >= week_ago:
                invoices_sent.append(
                    f"{record.number} — {item.client} — ${Money(record.amount_cents)}"
                )
    reviews = deps.store.open_reviews()
    data = SummaryData(
        week_of=today,
        timesheets_received=sorted(set(received)),
        invoices_sent=invoices_sent,
        waiting_for_review=[
            f"[{review.code}] {review.message}"
            for review in reviews
            if review.code != "UNKNOWN_SENDER"
        ],
        waiting_for_approval=[
            f"{item.consultant} at {item.client}, {format_period(item.period)}"
            for item in items
            if item.status is ItemStatus.WAITING_FOR_APPROVAL
        ],
        no_timesheet_yet=[
            f"{item.consultant} at {item.client}, {format_period(item.period)}"
            for item in items
            if item.status is ItemStatus.WAITING_FOR_TIMESHEET
            and item.period.end + timedelta(days=MISSING_TIMESHEET_GRACE_DAYS) <= today
        ],
        set_aside=[review.message for review in reviews if review.code == "UNKNOWN_SENDER"],
        duplicates_filed=[
            what for on, what in _duplicates_filed(deps) if date.fromisoformat(on) >= week_ago
        ],
        unpaid_past_due=_unpaid_past_due(deps),
        looks_stuck=_looks_stuck(deps),
    )
    attachment = EmailAttachment("tracking.xlsx", tracking_sha) if tracking_sha else None
    email = emails.monday_summary(deps.settings.admin_email, data, attachment)
    if enqueue_email(deps, "summary_email", f"summary:{today.isoformat()}", None, email):
        deps.store.set_state(LAST_SUMMARY_KEY, today.isoformat())
        report.note("Monday summary written down")


def _unpaid_past_due(deps: RunDeps) -> list[str] | None:
    """Invoices sent and not yet paid whose due date has gone by, oldest due
    first. Only QuickBooks can say an invoice was paid, so in manual mode the
    agent does not know and the section is left out (None) rather than
    listing every invoice it ever sent. Kevin chases payment, as today: the
    agent never writes to a client about it (objective 5)."""
    if not deps.accounting.can_look_up_invoices:
        return None
    today = deps.clock.today()
    late: list[tuple[date, str]] = []
    for item in deps.store.list_items():
        if item.status is not ItemStatus.INVOICE_SENT:
            continue
        for record in deps.store.invoices_for_item(item.id):
            if record.status == "sent" and record.due_date < today:
                days = (today - record.due_date).days
                late.append(
                    (
                        record.due_date,
                        f"{record.number} — {item.client} — ${Money(record.amount_cents)}, "
                        f"due {record.due_date} ({days} day{'s' if days != 1 else ''} ago)",
                    )
                )
    return [line for _, line in sorted(late)]


def _looks_stuck(deps: RunDeps) -> list[str]:
    """What the read-only diagnosis finds, with what to do about each. A
    QuickBooks that cannot be asked leaves the section out rather than the
    summary."""
    try:
        findings = diagnose_everything(looking_at(deps), None)
    except AccountingFailed:
        return []
    return [
        f"{finding.what} What to do: {finding.next_step}" if finding.next_step else finding.what
        for finding in findings
        if finding.needs_attention
    ]
