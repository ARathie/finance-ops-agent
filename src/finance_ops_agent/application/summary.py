"""The Monday summary and the tracking sheet, assembled from the store."""

from datetime import timedelta

from finance_ops_agent.application.context import RunDeps, RunReport
from finance_ops_agent.application.outgoing import enqueue_email
from finance_ops_agent.domain import emails
from finance_ops_agent.domain.emails import EmailAttachment, SummaryData, format_period
from finance_ops_agent.domain.money import Money
from finance_ops_agent.domain.statuses import ItemStatus
from finance_ops_agent.domain.tracking import TrackingRow

LAST_SUMMARY_KEY = "last_summary"
MISSING_TIMESHEET_GRACE_DAYS = 7


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
    )
    attachment = EmailAttachment("tracking.xlsx", tracking_sha) if tracking_sha else None
    email = emails.monday_summary(deps.settings.admin_email, data, attachment)
    if enqueue_email(deps, "summary_email", f"summary:{today.isoformat()}", None, email):
        deps.store.set_state(LAST_SUMMARY_KEY, today.isoformat())
        report.note("Monday summary written down")
