"""Everything that leaves the agent: planned, written down, then sent.

Every email is written to the outgoing table (once, by idempotency key) before
anything happens, sent draft-then-send, and reconciled after a restart: an
in-flight record is checked against the provider before any retry, so a crash
between "about to send" and "sent" still results in exactly one email.
"""

from datetime import timedelta

from finance_ops_agent.application.context import Mode, RunDeps, RunReport
from finance_ops_agent.domain import emails
from finance_ops_agent.domain.emails import EmailAttachment, OutgoingEmail
from finance_ops_agent.domain.guardrails import GuardrailCheck, check_guardrails
from finance_ops_agent.domain.invoices import DRAFT_NUMBER, build_invoice
from finance_ops_agent.domain.items import (
    InvoiceRecord,
    Item,
    OutgoingRecord,
    PaymentInstructionRecord,
)
from finance_ops_agent.domain.money import Money
from finance_ops_agent.domain.reading import TimesheetReading
from finance_ops_agent.domain.review import ReviewCode
from finance_ops_agent.domain.statuses import ItemStatus
from finance_ops_agent.ports.sender import DraftState

MAX_SEND_ATTEMPTS = 3

EMAIL_KINDS = (
    "details_email",
    "review_email",
    "approval_request",
    "billing_email",
    "payment_email",
    "preview_email",
    "summary_email",
    "ask_again_email",
)


def enqueue_email(
    deps: RunDeps,
    kind: str,
    key: str,
    item_id: int | None,
    email: OutgoingEmail,
    extra: dict[str, object] | None = None,
) -> bool:
    payload = email.payload()
    payload.update(extra or {})
    return deps.store.record_outgoing(kind, key, item_id, payload)


def _active_timesheet_attachment(deps: RunDeps, item: Item) -> EmailAttachment | None:
    records = [
        record
        for record in deps.store.timesheets_for_item(item.id)
        if not record.is_duplicate and not record.is_correction
    ]
    if not records:
        return None
    sha = records[-1].sha256
    filename = deps.store.file_name_for(sha) or f"timesheet-{sha[:8]}"
    return EmailAttachment(filename=filename, sha256=sha)


def _live_invoice_count(deps: RunDeps, item: Item) -> int:
    return len(deps.store.invoices_for_item(item.id))


def _recent_amounts(deps: RunDeps, item: Item) -> list[Money]:
    """What the last few invoices for this same engagement came to."""
    amounts: list[Money] = []
    for other in deps.store.list_items():
        if other.id == item.id:
            continue
        if other.consultant != item.consultant or other.client != item.client:
            continue
        for record in deps.store.invoices_for_item(other.id):
            if record.status != "cancelled":
                amounts.append(Money(record.amount_cents))
    return amounts


def guardrails_for(deps: RunDeps, item: Item) -> GuardrailCheck:
    """Every condition from docs/technical-design.md, for this one item."""
    assert item.invoice_amount is not None
    readings = [
        TimesheetReading.model_validate(record.reading)
        for record in deps.store.timesheets_for_item(item.id)
        if not record.is_duplicate and not record.is_correction
    ]
    return check_guardrails(
        send_automatically=item.snapshot.send_automatically,
        open_review_count=sum(
            1 for review in deps.store.open_reviews() if review.item_id == item.id
        ),
        readings=readings,
        amount=item.invoice_amount,
        recent_amounts=_recent_amounts(deps, item),
    )


def approve_item(deps: RunDeps, item: Item, report: RunReport) -> None:
    """Create the invoice (idempotently) and write down the billing email.

    The item's status advances to invoice_sent only once the billing email is
    actually sent (see advance_after_sends)."""
    replaced = [
        record.number
        for record in deps.store.invoices_for_item(item.id)
        if record.status == "cancelled"
    ]
    replaces = replaced[-1] if replaced else None
    # create_invoice is idempotent per item: an adapter returns the invoice it
    # already made rather than making a second one (in QuickBooks that means
    # recognising the item id in the invoice's private note). So this asks once
    # and never needs a lookup of its own.
    known = {record.external_id for record in deps.store.invoices_for_item(item.id)}
    draft = build_invoice(item, DRAFT_NUMBER, deps.clock.today(), replaces)
    created = deps.accounting.create_invoice(draft, item.id)
    invoice = build_invoice(item, created.number, deps.clock.today(), replaces)
    pdf_sha = deps.store.save_file(created.pdf)
    if created.external_id not in known:
        report.invoices_created += 1
        report.note(f"invoice {created.number} created for {item.consultant} at {item.client}")
        # The agent's own record of the invoice, written here rather than inside
        # an adapter so manual mode and QuickBooks Online behave identically.
        deps.store.record_invoice(
            InvoiceRecord(
                id=0,
                item_id=item.id,
                number=created.number,
                external_id=created.external_id,
                amount_cents=invoice.total.cents,
                issue_date=invoice.issue_date,
                due_date=invoice.due_date,
                pdf_sha256=pdf_sha,
                status="created",
                replaces_number=replaces,
            )
        )
    attachments = tuple(
        attachment
        for attachment in (
            EmailAttachment(f"invoice-{created.number}.pdf", pdf_sha),
            _active_timesheet_attachment(deps, item),
        )
        if attachment is not None
    )
    billing = emails.billing_email(deps.settings.admin_email, item, invoice, attachments)
    if item.snapshot.client_delivery == "portal":
        # Kevin uploads it to the client's portal himself; the package goes to him.
        billing = emails.dry_run_preview(
            deps.settings.admin_email, item, invoice, billing, attachments
        )
    enqueue_email(
        deps,
        "billing_email",
        f"billing:{item.id}:{created.number}",
        item.id,
        billing,
        # Both: the number is what Kevin and the client see, the external id is
        # what the accounting system and the agent's own records key on.
        {"invoice_number": created.number, "invoice_external_id": created.external_id},
    )


def _enqueue_payment(deps: RunDeps, item: Item, invoice_number: str | None) -> None:
    assert item.amount_owed is not None
    due = item.period.end + timedelta(days=item.snapshot.pay_timing_days)
    key = f"payment:{item.id}:{item.amount_owed.cents}"
    email = emails.payment_instruction(deps.settings.admin_email, item, due, invoice_number)
    if enqueue_email(deps, "payment_email", key, item.id, email):
        deps.store.record_payment_instruction(
            PaymentInstructionRecord(
                item_id=item.id,
                payee=item.snapshot.payee,
                amount_cents=item.amount_owed.cents,
                due_date=due,
                method=item.snapshot.paid_by,
            )
        )


def plan_outgoing(deps: RunDeps, report: RunReport) -> None:
    """Decide, mode by mode, what each ready item causes to be written down."""
    mode = deps.settings.mode
    for item in deps.store.list_items():
        if item.status is not ItemStatus.READY:
            continue
        assert item.approved_hours is not None and item.invoice_amount is not None
        if mode is Mode.DRY_RUN:
            invoice = build_invoice(item, "DRAFT", deps.clock.today())
            pdf_sha = deps.store.save_file(deps.renderer.invoice_pdf(invoice))
            attachments = tuple(
                attachment
                for attachment in (
                    EmailAttachment("invoice-DRAFT.pdf", pdf_sha),
                    _active_timesheet_attachment(deps, item),
                )
                if attachment is not None
            )
            billing = emails.billing_email(deps.settings.admin_email, item, invoice, attachments)
            preview = emails.dry_run_preview(
                deps.settings.admin_email, item, invoice, billing, attachments
            )
            key = f"preview:{item.id}:{item.approved_hours.hundredths}"
            enqueue_email(deps, "preview_email", key, item.id, preview)
            _enqueue_payment(deps, item, None)
            # Dry run stops here, always: nothing to a client, nothing created.
            continue
        # Only automatic mode can skip asking, and only when every guardrail
        # holds. Anything else is handled as ask first: the invoice still
        # happens, Kevin just sees it first.
        guardrails = guardrails_for(deps, item) if mode is Mode.AUTO else None
        if guardrails is not None and guardrails.may_send_automatically:
            approve_item(deps, item, report)
        else:
            if guardrails is not None:
                report.note(
                    f"asking rather than sending automatically ({item.consultant}"
                    f" at {item.client}): {guardrails.why_not()}"
                )
            invoice = build_invoice(item, DRAFT_NUMBER, deps.clock.today())
            pdf_sha = deps.store.save_file(deps.renderer.invoice_pdf(invoice))
            attachments = tuple(
                attachment
                for attachment in (
                    EmailAttachment("proposed-invoice.pdf", pdf_sha),
                    _active_timesheet_attachment(deps, item),
                )
                if attachment is not None
            )
            billing = emails.billing_email(deps.settings.admin_email, item, invoice, attachments)
            request = emails.approve_invoice(
                deps.settings.admin_email, item, invoice, billing, attachments
            )
            key = (
                f"approve:{item.id}:{_live_invoice_count(deps, item)}"
                f":{item.approved_hours.hundredths}"
            )
            if enqueue_email(deps, "approval_request", key, item.id, request):
                report.note(f"asking Kevin to approve: {request.subject}")
            deps.store.change_status(item.id, ItemStatus.WAITING_FOR_APPROVAL, {})


def advance_after_sends(deps: RunDeps, report: RunReport) -> None:
    """A billing email that actually went out makes the item invoice_sent and
    releases the payment instruction. Safe to re-run: everything is keyed."""
    for record in deps.store.outgoing_records():
        if record.kind != "billing_email" or record.status != "done":
            continue
        if record.item_id is None:
            continue
        item = deps.store.get_item(record.item_id)
        if item.status in (ItemStatus.READY, ItemStatus.WAITING_FOR_APPROVAL):
            item = deps.store.change_status(item.id, ItemStatus.INVOICE_SENT, {})
            report.note(f"invoice sent: {item.consultant} at {item.client}")
        if item.status is ItemStatus.INVOICE_SENT:
            number = record.payload.get("invoice_number")
            external_id = record.payload.get("invoice_external_id")
            if isinstance(external_id, str):
                deps.store.set_invoice_status(external_id, "sent")
            _enqueue_payment(deps, item, number if isinstance(number, str) else None)


def _load_attachments(deps: RunDeps, email: OutgoingEmail) -> dict[str, bytes]:
    return {
        attachment.filename: deps.store.load_file(attachment.sha256)
        for attachment in email.attachments
    }


def _open_send_failed_review(deps: RunDeps, record: OutgoingRecord, report: RunReport) -> None:
    deps.store.open_review(
        record.item_id,
        ReviewCode.SEND_FAILED.value,
        "I couldn't send the billing email. I'll keep trying; please check the mailbox."
        if record.kind == "billing_email"
        else f"I couldn't send an email ({record.kind}). Please check the mailbox.",
    )
    report.note(f"giving up after {record.attempts} attempts: {record.idempotency_key}")


def send_pending(deps: RunDeps, report: RunReport) -> None:
    """Reconcile anything in flight, then work through the pending emails."""
    for record in deps.store.outgoing_records():
        if record.kind not in EMAIL_KINDS:
            continue
        if record.status == "in_flight":
            _reconcile(deps, record, report)
    for record in deps.store.outgoing_records():
        if record.kind not in EMAIL_KINDS or record.status != "pending":
            continue
        _send_one(deps, record, report)


def _reconcile(deps: RunDeps, record: OutgoingRecord, report: RunReport) -> None:
    """Before any retry, ask the provider what really happened to this draft."""
    if record.draft_id is None:
        deps.store.update_outgoing(record.idempotency_key, status="pending")
        return
    state = deps.sender.find_draft(record.draft_id)
    if state is DraftState.SENT:
        # The provider sent it before the crash: write that down, never resend.
        deps.store.update_outgoing(record.idempotency_key, status="done")
        report.note(f"already sent before the restart: {record.idempotency_key}")
    elif state is DraftState.STILL_DRAFT:
        _attempt_send(deps, record, record.draft_id, report)
    else:
        deps.store.update_outgoing(record.idempotency_key, status="pending")


def _attempt_send(deps: RunDeps, record: OutgoingRecord, draft_id: str, report: RunReport) -> None:
    """Send an existing draft, counting the attempt and giving up after
    MAX_SEND_ATTEMPTS with a SEND_FAILED review."""
    try:
        deps.sender.send(draft_id)
    except Exception as error:
        updated = deps.store.update_outgoing(
            record.idempotency_key, error=str(error), bump_attempts=True
        )
        if updated.attempts >= MAX_SEND_ATTEMPTS:
            deps.store.update_outgoing(record.idempotency_key, status="failed")
            _open_send_failed_review(deps, updated, report)
        # Otherwise the record stays in_flight and the next run reconciles it.
        raise
    deps.store.update_outgoing(record.idempotency_key, status="done")
    report.emails_sent += 1


def _send_one(deps: RunDeps, record: OutgoingRecord, report: RunReport) -> None:
    email = OutgoingEmail.from_payload(record.payload)
    try:
        draft_id = deps.sender.create_draft(email, _load_attachments(deps, email))
    except Exception as error:
        updated = deps.store.update_outgoing(
            record.idempotency_key, error=str(error), bump_attempts=True
        )
        if updated.attempts >= MAX_SEND_ATTEMPTS:
            deps.store.update_outgoing(record.idempotency_key, status="failed")
            _open_send_failed_review(deps, updated, report)
        raise
    # The draft id is written down before the send, so a crash in between is
    # reconciled against the provider rather than retried blindly.
    deps.store.update_outgoing(record.idempotency_key, status="in_flight", draft_id=draft_id)
    _attempt_send(deps, record, draft_id, report)
