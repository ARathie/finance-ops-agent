"""Everything that leaves the agent: planned, written down, then sent.

Every email is written to the outgoing table (once, by idempotency key) before
anything happens. The agent makes the Message-ID and writes it down, hands the
email to the mail server, records the moment the server accepted it, then
files a copy in Sent. After a restart an in-flight record is reconciled before
any retry: found in Sent means done; not found and older than the grace period
means the agent asks Kevin (who is on CC) rather than guess
(docs/integrations/email-imap-smtp.md, "Never twice, with SMTP").
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from email.utils import make_msgid

from finance_ops_agent.application.context import Mode, RunDeps, RunReport
from finance_ops_agent.domain import emails
from finance_ops_agent.domain.emails import EmailAttachment, OutgoingEmail
from finance_ops_agent.domain.guardrails import GuardrailCheck, check_guardrails
from finance_ops_agent.domain.invoice_numbers import invoice_number
from finance_ops_agent.domain.invoices import Invoice, build_invoice
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
from finance_ops_agent.ports.accounting import (
    AccountingFailed,
    AccountingNeedsReconnect,
    CreatedInvoice,
)
from finance_ops_agent.ports.sender import NotSent, RecipientRefused

MAX_SEND_ATTEMPTS = 3
UNCERTAIN_AFTER = timedelta(minutes=10)

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


MAX_NUMBER_ATTEMPTS = 20


def next_invoice_number(deps: RunDeps, item: Item) -> str | None:
    """Kevin's number for this item: `083126MT-PS` (domain/invoice_numbers.py).

    One invoice per consultant per client per month makes the plain number
    unique, so the only thing normally standing in its way is a correction:
    the replacement covers the same period as the invoice it replaces, whose
    number is spent, so it takes the next one along.

    None means the agent cannot number it and has asked Kevin instead. Nothing
    is invented: the two letters are the client's own and the initials are the
    consultant's.
    """
    client_code = item.snapshot.client_invoice_code
    consultant_code = item.snapshot.consultant_code
    if not client_code or not consultant_code:
        missing = (
            f'the Clients sheet has no "Invoice code" for {item.client}'
            if not client_code
            else f"I can't work out {item.consultant}'s initials from their name"
        )
        deps.store.open_review(
            item.id,
            ReviewCode.LIST_ROW_PROBLEM.value,
            f"I can't number the invoice for {item.consultant} at {item.client}:"
            f" {missing}. Fill it in on the engagement list and I'll pick this up"
            " on the next run. Nothing was created or sent.",
        )
        return None
    for attempt in range(1, MAX_NUMBER_ATTEMPTS + 1):
        candidate = invoice_number(item.period.end, client_code, consultant_code, attempt)
        if not deps.store.invoice_number_in_use(candidate):
            return candidate
    deps.store.open_review(
        item.id,
        ReviewCode.LIST_ROW_PROBLEM.value,
        f"I have already used every invoice number I can make for {item.consultant}"
        f" at {item.client} for {item.period.start} to {item.period.end}."
        " Nothing was created or sent.",
    )
    return None


def tell_kevin(
    deps: RunDeps, report: RunReport, code: ReviewCode, about: str, message: str, key: str
) -> None:
    """Open the review and write the email that carries it.

    A review row on its own is only a note to the agent: reviews become emails
    where they are raised, so one raised here has to bring its own.
    """
    item_id = None  # these are about the connection, not one timesheet
    if not deps.store.open_review(item_id, code.value, message):
        return  # already open from an earlier run; Kevin has been told once
    report.reviews_opened += 1
    email = emails.needs_review(deps.settings.admin_email, about, [message])
    enqueue_email(deps, "review_email", f"review:{key}", item_id, email)


def _create_invoice(
    deps: RunDeps, draft: Invoice, item: Item, report: RunReport
) -> CreatedInvoice | None:
    """Ask the accounting system for the invoice, or tell Kevin why not.

    A QuickBooks problem is a question for Kevin, not the end of the run: the
    other timesheets in this run still get read, still get their emails, and
    the invoice is made next run once the connection is back. None means it did
    not happen and a review has been opened.
    """
    try:
        return deps.accounting.create_invoice(draft, item.id)
    except AccountingNeedsReconnect as error:
        # Every other item this run would fail the same way, and each one would
        # mean another go at the token endpoint.
        report.quickbooks_unavailable = True
        tell_kevin(
            deps,
            report,
            ReviewCode.QUICKBOOKS_RECONNECT,
            "QuickBooks",
            "QuickBooks needs to be reconnected, so I have not made any invoices this"
            " time and nothing went to a client. Run `fops qbo-connect` and I will pick"
            f" them up on the next run. QuickBooks said: {error}",
            "quickbooks-reconnect",
        )
        report.note(f"QuickBooks needs reconnecting; no invoice for {item.consultant}")
        return None
    except AccountingFailed as error:
        message = (
            f"I could not make the invoice for {item.consultant} at {item.client}"
            f" ({item.period.start} to {item.period.end}) in QuickBooks, so nothing"
            f" went to the client. I will try again next run. QuickBooks said: {error}"
        )
        if deps.store.open_review(item.id, ReviewCode.QUICKBOOKS_FAILED.value, message):
            report.reviews_opened += 1
            email = emails.needs_review(
                deps.settings.admin_email, f"{item.consultant} — {item.client}", [message]
            )
            enqueue_email(deps, "review_email", f"review:quickbooks:{item.id}", item.id, email)
        report.note(f"QuickBooks would not make the invoice for {item.consultant}: {error}")
        return None


@dataclass(frozen=True)
class PreparedInvoice:
    """The real invoice, and everything an email about it needs."""

    invoice: Invoice
    created: CreatedInvoice
    attachments: tuple[EmailAttachment, ...]


def prepare_invoice(deps: RunDeps, item: Item, report: RunReport) -> PreparedInvoice | None:
    """Make the invoice in the accounting system, and write it down.

    Made before Kevin is asked, not after, so the invoice he approves is the
    one the client will receive -- the rate comes off the product in
    QuickBooks and the PDF is rendered by his own invoice template, so a
    stand-in drawn here would be approving something else (decision 33).

    Idempotent per item: the adapter returns the invoice it already made
    rather than making a second one, so asking and then approving makes one
    invoice, and a restart in between makes none. None means it did not happen
    and Kevin has been told why.
    """
    replaced = [
        record.number
        for record in deps.store.invoices_for_item(item.id)
        if record.status == "cancelled"
    ]
    replaces = replaced[-1] if replaced else None
    known = {record.external_id for record in deps.store.invoices_for_item(item.id)}
    # The number is the agent's to assign in both modes, so it reads the same
    # whether Kevin types it into QuickBooks Desktop or QuickBooks Online was
    # told to use it (docs/decisions.md #27).
    number = next_invoice_number(deps, item)
    if number is None:
        return None  # Kevin has been asked; nothing created, nothing sent
    draft = build_invoice(item, number, deps.clock.today(), replaces)
    created = _create_invoice(deps, draft, item, report)
    if created is None:
        return None  # Kevin has been told; nothing created, nothing sent
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
    return PreparedInvoice(invoice=invoice, created=created, attachments=attachments)


def cancel_invoices(deps: RunDeps, item: Item, report: RunReport) -> None:
    """Void whatever was already made for this item.

    QuickBooks has no draft invoices, so one Kevin cancels cannot be taken back
    out of the books: it is voided, and its number stays spent. If the voiding
    itself fails, Kevin is told so he can do it by hand -- the item is still
    cancelled either way, because he said so.
    """
    for record in deps.store.invoices_for_item(item.id):
        if record.status == "cancelled":
            continue
        try:
            deps.accounting.cancel_invoice(record.external_id)
        except AccountingFailed as error:
            message = (
                f"Kevin cancelled {item.consultant} at {item.client}, but I could not"
                f" void invoice {record.number} in QuickBooks. Void it there by hand."
                f" QuickBooks said: {error}"
            )
            if deps.store.open_review(item.id, ReviewCode.QUICKBOOKS_FAILED.value, message):
                report.reviews_opened += 1
                email = emails.needs_review(
                    deps.settings.admin_email, f"{item.consultant} — {item.client}", [message]
                )
                enqueue_email(
                    deps, "review_email", f"review:void:{record.external_id}", item.id, email
                )
            report.note(f"could not void invoice {record.number}: {error}")
            continue
        deps.store.set_invoice_status(record.external_id, "cancelled")
        report.note(f"voided invoice {record.number}")


def approve_item(deps: RunDeps, item: Item, report: RunReport) -> None:
    """Write down the billing email for an invoice that already exists.

    The item's status advances to invoice_sent only once the billing email is
    actually sent (see advance_after_sends)."""
    prepared = prepare_invoice(deps, item, report)
    if prepared is None:
        return
    invoice, created = prepared.invoice, prepared.created
    billing = emails.billing_email(deps.settings.admin_email, item, invoice, prepared.attachments)
    if item.snapshot.client_delivery == "portal":
        # Kevin uploads it to the client's portal himself; the package goes to him.
        billing = emails.dry_run_preview(
            deps.settings.admin_email, item, invoice, billing, prepared.attachments
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
        if report.quickbooks_unavailable:
            continue  # the connection is down; Kevin already has the review
        guardrails = guardrails_for(deps, item) if mode is Mode.AUTO else None
        if guardrails is not None and guardrails.may_send_automatically:
            approve_item(deps, item, report)
        else:
            if guardrails is not None:
                report.note(
                    f"asking rather than sending automatically ({item.consultant}"
                    f" at {item.client}): {guardrails.why_not()}"
                )
            # The invoice is made now, before Kevin is asked, so what he
            # approves is the invoice the client will get: the rate comes off
            # the product in QuickBooks and the PDF is his own template
            # (decision 33). Nothing is sent until he answers, and cancelling
            # voids it.
            prepared = prepare_invoice(deps, item, report)
            if prepared is None:
                continue  # Kevin has been told why; nothing to ask about yet
            invoice, attachments = prepared.invoice, prepared.attachments
            billing = emails.billing_email(deps.settings.admin_email, item, invoice, attachments)
            request = emails.approve_invoice(
                deps.settings.admin_email, item, invoice, billing, attachments
            )
            key = (
                f"approve:{item.id}:{prepared.created.external_id}:{item.approved_hours.hundredths}"
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


def _now_text(deps: RunDeps) -> str:
    return deps.clock.now().isoformat()


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
        if record.kind in EMAIL_KINDS and record.status == "in_flight":
            _reconcile(deps, record, report)
    for record in deps.store.outgoing_records():
        if record.kind in EMAIL_KINDS and record.status == "pending":
            _send_one(deps, record, report)


def _reconcile(deps: RunDeps, record: OutgoingRecord, report: RunReport) -> None:
    """Before any retry, find out what really happened to an in-flight email."""
    key = record.idempotency_key
    if record.message_id is None:
        deps.store.update_outgoing(key, status="pending")  # never got as far as a send
        return
    if record.accepted_at is not None:
        _file_copy_and_finish(deps, record)  # sent; only the Sent copy was left to do
        return
    if deps.sender.find_sent(record.message_id):
        deps.store.update_outgoing(key, accepted_at=_now_text(deps), status="done")
        report.note(f"already sent before the restart: {key}")
        return
    started = datetime.fromisoformat(record.started_at) if record.started_at else None
    if started is None or deps.clock.now() - started < UNCERTAIN_AFTER:
        return  # too soon to tell; the next run looks again
    _ask_whether_it_arrived(deps, record, report)


def _file_copy_and_finish(deps: RunDeps, record: OutgoingRecord) -> None:
    """The server took the email; put a copy in Sent (a courtesy) and close out."""
    assert record.message_id is not None
    email = OutgoingEmail.from_payload(record.payload)
    try:
        deps.sender.save_sent_copy(email, _load_attachments(deps, email), record.message_id)
    except Exception as error:
        # Sent already; the copy is retried next run and nothing is resent.
        deps.store.update_outgoing(
            record.idempotency_key, error=f"sent, but the copy to Sent failed: {error}"
        )
        return
    deps.store.update_outgoing(record.idempotency_key, status="done")


def _uncertain_problem(record: OutgoingRecord) -> str:
    subject = str(record.payload.get("subject", ""))
    return (
        f"I sent \"{subject}\" but couldn't confirm it left the server. You're on CC:"
        ' reply "received" if you got it, or "resend".'
    )


def _ask_whether_it_arrived(deps: RunDeps, record: OutgoingRecord, report: RunReport) -> None:
    """Older than the grace period and not in Sent: ask Kevin, never guess."""
    subject = str(record.payload.get("subject", ""))
    problem = _uncertain_problem(record)
    if not deps.store.open_review(record.item_id, ReviewCode.SEND_UNCERTAIN.value, problem):
        return  # already asked
    ask = emails.needs_review(deps.settings.admin_email, subject, [problem])
    enqueue_email(
        deps,
        "review_email",
        f"review:uncertain:{record.idempotency_key}",
        record.item_id,
        ask,
        {"uncertain_key": record.idempotency_key},
    )
    report.note(f"asking Kevin whether it arrived: {subject}")


def answer_send_uncertain(
    deps: RunDeps, review_record: OutgoingRecord, body: str, report: RunReport
) -> None:
    """Kevin answered a SEND_UNCERTAIN review: "received" closes it, "resend"
    sends the same email again under the same Message-ID."""
    key = str(review_record.payload.get("uncertain_key", ""))
    records = {record.idempotency_key: record for record in deps.store.outgoing_records()}
    record = records.get(key)
    if record is None:
        return
    first = body.strip().split()[0].strip('".,!').casefold() if body.strip() else ""
    # Only this email's question: an item can have several emails in doubt at once.
    reviews = [
        review
        for review in deps.store.open_reviews()
        if review.code == ReviewCode.SEND_UNCERTAIN.value
        and review.item_id == record.item_id
        and review.message == _uncertain_problem(record)
    ]
    if first == "received":
        if record.status == "in_flight":
            deps.store.update_outgoing(key, accepted_at=_now_text(deps), status="done")
        for review in reviews:
            deps.store.answer_review(review.id, {"kind": "received"}, "answered")
        report.note(f"Kevin confirmed it arrived: {key}")
    elif first == "resend":
        if record.status == "in_flight":
            deps.store.update_outgoing(key, status="pending", clear_times=True)
        for review in reviews:
            deps.store.answer_review(review.id, {"kind": "resend"}, "answered")
        report.note(f"Kevin asked for a resend: {key}")
    else:
        ask = OutgoingEmail(
            to=(deps.settings.admin_email,),
            subject=f"Re: {review_record.payload.get('subject', '')}",
            body='Sorry - on this one I only understand "received" or "resend".',
        )
        enqueue_email(
            deps, "ask_again_email", f"askword:{key}:{record.attempts}", record.item_id, ask
        )


def _new_message_id(deps: RunDeps) -> str:
    domain = deps.settings.agent_mailbox.rsplit("@", 1)[-1] or "finance-ops-agent"
    return make_msgid(domain=domain)


def _send_one(deps: RunDeps, record: OutgoingRecord, report: RunReport) -> None:
    key = record.idempotency_key
    email = OutgoingEmail.from_payload(record.payload)
    attachments = _load_attachments(deps, email)
    # The Message-ID is written down before anything is sent, so a crash in
    # between is reconciled against the Sent folder rather than retried blindly.
    message_id = record.message_id or _new_message_id(deps)
    deps.store.update_outgoing(
        key, status="in_flight", message_id=message_id, started_at=_now_text(deps)
    )
    try:
        deps.sender.send(email, attachments, message_id)
    except NotSent as error:
        # The server took nothing: count the attempt and try again next run.
        updated = deps.store.update_outgoing(
            key, status="pending", error=str(error), bump_attempts=True, clear_times=True
        )
        if isinstance(error, RecipientRefused) or updated.attempts >= MAX_SEND_ATTEMPTS:
            deps.store.update_outgoing(key, status="failed")
            _open_send_failed_review(deps, updated, report)
            return  # a bad address stops this email, not the run
        raise
    except Exception as error:
        # Ambiguous: the server may have it. Stay in flight for the reconcile.
        deps.store.update_outgoing(key, error=str(error))
        raise
    accepted = deps.store.update_outgoing(key, accepted_at=_now_text(deps))
    report.emails_sent += 1
    _file_copy_and_finish(deps, accepted)
