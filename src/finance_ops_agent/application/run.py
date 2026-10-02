"""One run of the agent, on ports only: ingest mail, check timesheets, keep records.

The order follows docs/technical-design.md. Never twice, at every step:
messages are stored before they are processed and marked processed only when
handling finished; the mailbox position is saved only after the run's messages
are stored; a second run over the same mailbox changes nothing.

Everything that leaves the agent is composed here, written to the outgoing
table once per thing, and sent draft-then-send with restart reconciliation
(application/outgoing.py). Kevin's replies are matched and applied
(application/replies.py). The tracking sheet is rewritten after every run.
"""

import hashlib
from dataclasses import dataclass
from datetime import date

from finance_ops_agent import logs
from finance_ops_agent.application import outgoing as outgoing_steps
from finance_ops_agent.application import paid_check, set_aside
from finance_ops_agent.application import replies as reply_steps
from finance_ops_agent.application import summary as summary_steps
from finance_ops_agent.application.completion import complete_if_covered
from finance_ops_agent.application.context import Mode, RunDeps, RunReport, Settings
from finance_ops_agent.application.engagement_copy import Engagements
from finance_ops_agent.application.senders import decide_kind
from finance_ops_agent.domain import checks, emails
from finance_ops_agent.domain.checks import Finding, same_addresses, split_addresses
from finance_ops_agent.domain.emails import EmailAttachment, TimesheetSummary
from finance_ops_agent.domain.engagements import (
    Client,
    Consultant,
    ConsultantType,
    Delivery,
    Engagement,
    EngagementWorkbook,
    ListRowProblem,
    Vendor,
)
from finance_ops_agent.domain.invoice_numbers import codes_for_client
from finance_ops_agent.domain.items import EngagementSnapshot, Item, TimesheetRecord
from finance_ops_agent.domain.live_engagements import LiveEngagements, live_engagements
from finance_ops_agent.domain.messages import (
    InboundEmail,
    MessageKind,
    StoredAttachment,
    StoredMessage,
)
from finance_ops_agent.domain.money import Money
from finance_ops_agent.domain.periods import BillingPeriod, billing_periods
from finance_ops_agent.domain.reading import ReadingHints, TimesheetReading
from finance_ops_agent.domain.review import ReviewCode
from finance_ops_agent.domain.statuses import ItemStatus
from finance_ops_agent.ports.accounting import (
    AccountingFailed,
    AccountingNeedsReconnect,
    AccountingParty,
    AccountingSystem,
    EngagementRates,
)
from finance_ops_agent.ports.inbox import IGNORED_FOLDER, NEEDS_REVIEW_FOLDER, PROCESSED_FOLDER
from finance_ops_agent.ports.reader import CantReadAttachmentError

MAILBOX_POSITION_KEY = "mailbox_position"
LAST_EXPECTED_CHECK_KEY = "last_expected_check"

__all__ = ["Mode", "RunDeps", "RunReport", "Settings", "run_once"]


def run_once(deps: RunDeps, report: RunReport | None = None) -> RunReport:
    report = report or RunReport()
    logs.log("run started", mode=deps.settings.mode.value)
    _engagement_work(deps, report)
    outgoing_steps.plan_outgoing(deps, report)
    paid_check.check_paid_invoices(deps, report)
    tracking_sha = _write_tracking(deps)
    summary_steps.enqueue_monday_summary(deps, report, tracking_sha)
    outgoing_steps.send_pending(deps, report)
    outgoing_steps.advance_after_sends(deps, report)
    outgoing_steps.send_pending(deps, report)  # what advancing released, e.g. payment
    _write_tracking(deps)  # once more, now with the run's final statuses
    logs.log(
        "run finished",
        messages_stored=report.messages_stored,
        timesheets_processed=report.timesheets_processed,
        reviews_opened=report.reviews_opened,
        items_made_ready=report.items_made_ready,
        emails_sent=report.emails_sent,
        invoices_created=report.invoices_created,
        duplicates_filed=report.duplicates_filed,
    )
    return report


def _engagement_work(deps: RunDeps, report: RunReport) -> None:
    """Everything that needs the engagement list.

    The list comes from the agent's own copy of QuickBooks, taken once a day
    and again whenever something does not match it (decision 55), so a run
    over a quiet mailbox asks QuickBooks nothing (decision 54). In list mode
    the spreadsheet is read every run, which costs nothing.
    """
    today = deps.clock.today().isoformat()
    position = deps.store.get_state(MAILBOX_POSITION_KEY)
    fetched, new_position = deps.inbox.new_messages(position)
    engagements = Engagements(deps, report)
    _report_list_problems(deps, engagements.workbook, report)
    if deps.store.get_state(LAST_EXPECTED_CHECK_KEY) != today:
        asked = _create_expected_items(deps, engagements.workbook, report)
        # Not marked done when QuickBooks could not be asked: the next run
        # tries again rather than letting an outage hide a period for a day.
        if engagements.complete and asked:
            deps.store.set_state(LAST_EXPECTED_CHECK_KEY, today)
    _ingest_mailbox(deps, engagements, fetched, new_position, report)
    _retry_unconfirmed(deps, engagements, report)
    _process_messages(deps, engagements, report)
    # After the messages, so a "try again" Kevin sent this run is acted on now.
    if set_aside.look_again(deps, engagements, report):
        _process_messages(deps, engagements, report)


def _write_tracking(deps: RunDeps) -> str | None:
    if deps.render_tracking is None:
        return None
    content = deps.render_tracking(summary_steps.tracking_rows(deps))
    if deps.tracking_path is not None:
        deps.tracking_path.parent.mkdir(parents=True, exist_ok=True)
        deps.tracking_path.write_bytes(content)
    return deps.store.save_file(content)


def _open_review(deps: RunDeps, report: RunReport, item_id: int | None, finding: Finding) -> None:
    if deps.store.open_review(item_id, finding.code.value, finding.message):
        report.reviews_opened += 1
        report.note(f"needs Kevin's review ({finding.code.value}): {finding.message}")
        logs.log("review opened", item_id=item_id, code=finding.code.value)


def describe_problem(problem: ListRowProblem) -> str:
    """Where a problem is, as Kevin would go and find it."""
    if problem.row_number == 0:
        return f"{problem.sheet}: {problem.message}"
    return f"{problem.sheet} sheet, row {problem.row_number}: {problem.message}"


def _report_list_problems(deps: RunDeps, workbook: EngagementWorkbook, report: RunReport) -> None:
    problems = [describe_problem(problem) for problem in workbook.problems]
    for message in problems:
        _open_review(deps, report, None, Finding(ReviewCode.LIST_ROW_PROBLEM, message))
    if problems:
        digest = hashlib.sha256("|".join(sorted(problems)).encode()).hexdigest()[:16]
        email = emails.needs_review(deps.settings.admin_email, "the engagement list", problems)
        outgoing_steps.enqueue_email(deps, "review_email", f"list-problems:{digest}", None, email)


def _engagement_pairs(
    workbook: EngagementWorkbook,
) -> dict[tuple[str, str], list[Engagement]]:
    pairs: dict[tuple[str, str], list[Engagement]] = {}
    for engagement in workbook.engagements:
        key = (engagement.consultant, engagement.client)
        pairs.setdefault(key, []).append(engagement)
    return pairs


def _pair_window(rows: list[Engagement]) -> tuple[date, date | None, Engagement]:
    """The whole engagement's dates across its rate rows, and the latest row
    (which carries the schedule)."""
    start = min(row.start_date for row in rows)
    end: date | None = None
    if all(row.end_date is not None for row in rows):
        end = max(row.end_date for row in rows if row.end_date is not None)
    latest = max(rows, key=lambda row: row.rates_from)
    return start, end, latest


def _client_by_name(workbook: EngagementWorkbook, name: str) -> Client | None:
    for client in workbook.clients:
        if checks.names_match(client.name, name):
            return client
    return None


def _consultant_by_name(workbook: EngagementWorkbook, name: str) -> Consultant | None:
    for consultant in workbook.consultants:
        if checks.names_match(consultant.name, name):
            return consultant
    return None


def _vendor_by_name(workbook: EngagementWorkbook, name: str) -> Vendor | None:
    for vendor in workbook.vendors:
        if checks.names_match(vendor.company, name):
            return vendor
    return None


def _consultant_code(workbook: EngagementWorkbook, rate_row: Engagement) -> str:
    """The consultant's part of the invoice number, for this client.

    Worked out across everyone engaged at the client, because two consultants
    who share initials there both change to the longer form
    (domain/invoice_numbers.py). Blank when the name gives nothing to work
    with, which the caller turns into a review.
    """
    peers = {
        engagement.consultant
        for engagement in workbook.engagements
        if checks.names_match(engagement.client, rate_row.client)
    }
    overrides: dict[str, str] = {}
    for name in peers:
        consultant = _consultant_by_name(workbook, name)
        if consultant is not None:
            overrides[consultant.name] = consultant.initials
    codes = codes_for_client(overrides)
    for name, code in codes.items():
        if checks.names_match(name, rate_row.consultant):
            return code
    return ""


def build_snapshot(
    workbook: EngagementWorkbook,
    rate_row: Engagement,
    accounting: AccountingSystem | None = None,
    failures: list[AccountingFailed] | None = None,
) -> EngagementSnapshot | None:
    """The engagement row as the item will remember it.

    Both rates and the payee come from the engagement's product in the
    accounting system where it has them, and from the engagement list where it
    does not (docs/decisions.md #38 and #43). The engagement list stays the
    cross-check: a disagreement is recorded on the snapshot so the caller can
    ask Kevin, and QuickBooks' figure is the one used meanwhile.

    The bill rate is the one that must be right before anything leaves: it is
    what the client is charged and what Kevin approves. Taking it here means a
    rate that has drifted is found when the timesheet is read, rather than by
    creating the invoice and voiding it.

    Where the accounting system could not be asked, what it said is added to
    `failures`, so a caller with a timesheet in hand can tell Kevin rather than
    invoice on figures nobody confirmed (decision 55)."""
    client = _client_by_name(workbook, rate_row.client)
    consultant = _consultant_by_name(workbook, rate_row.consultant)
    if client is None or consultant is None:
        return None
    if consultant.type is ConsultantType.VENDOR:
        vendor = _vendor_by_name(workbook, consultant.vendor_company)
        if vendor is None:
            return None
        payee, paid_by, pay_timing = vendor.company, vendor.paid_by, vendor.pay_timing_days
    else:
        payee, paid_by, pay_timing = (
            consultant.name,
            consultant.paid_by,
            (consultant.pay_timing_days),
        )
    bill_rate_cents = rate_row.bill_rate.cents
    pay_rate_cents = rate_row.pay_rate.cents
    said: list[str] = []
    engagement_ref = ""
    rates = _rates_from_accounting(accounting, rate_row, client, failures)
    who = f"{rate_row.consultant} at {rate_row.client}"
    if rates is not None:
        engagement_ref = rates.ref
        if rates.bill_rate_cents != bill_rate_cents:
            # Caught here rather than by the invoice: before this, a drifted
            # rate was only found by creating the invoice, seeing the total
            # disagree and voiding it, which spent a number and left a voided
            # invoice in the books for a spreadsheet nobody had updated
            # (decision 43).
            said.append(
                f"QuickBooks charges ${Money(rates.bill_rate_cents)} an hour for"
                f" {who} and the engagement list says"
                f" ${Money(bill_rate_cents)}. I am using QuickBooks' figure, which is"
                " where the rate lives now. Make them agree."
            )
        bill_rate_cents = rates.bill_rate_cents
    if rates is not None and rates.pay_rate_cents is not None:
        if rates.pay_rate_cents != pay_rate_cents:
            said.append(
                f"QuickBooks pays {who}"
                f" ${Money(rates.pay_rate_cents)} an hour and the engagement list says"
                f" ${Money(pay_rate_cents)}. I am using QuickBooks' figure, which is"
                " where the rate lives now. Make them agree."
            )
        pay_rate_cents = rates.pay_rate_cents
    if rates is not None and rates.payee and rates.payee != payee:
        said.append(
            f"QuickBooks pays {rates.payee} for {who} and the engagement list says"
            f" {payee}. I am using QuickBooks' answer. Make them agree."
        )
        payee = rates.payee
    # Where invoices go, how long the client has, and how long Icon has to pay:
    # QuickBooks' customer and vendor records where they say, the engagement
    # list where they are blank, and Kevin told where the two disagree
    # (decision 52).
    billing_emails = list(client.billing_emails)
    payment_terms_days = client.payment_terms_days
    legal_name = client.legal_name
    held = _party_from_accounting(
        accounting, "customer", client.quickbooks_customer or legal_name, failures
    )
    if held is not None:
        legal_name = held.company or held.name or legal_name
        held_emails = split_addresses(held.email)
        if held_emails and not same_addresses(held_emails, billing_emails):
            said.append(
                f"QuickBooks sends {client.name}'s invoices to {', '.join(held_emails)} and"
                f" the engagement list says {', '.join(billing_emails) or 'nowhere'}. I am"
                " using QuickBooks' addresses. Make them agree."
            )
        billing_emails = held_emails or billing_emails
        if held.payment_terms_days is not None:
            if held.payment_terms_days != payment_terms_days:
                said.append(
                    f"QuickBooks gives {client.name} {held.payment_terms_days} day(s) to pay"
                    f" and the engagement list says {payment_terms_days}. I am using"
                    " QuickBooks' terms. Make them agree."
                )
            payment_terms_days = held.payment_terms_days
    paid_to = _party_from_accounting(
        accounting, "payee", rates.payee_ref if rates else "", failures
    )
    if paid_to is not None and paid_to.payment_terms_days is not None:
        if paid_to.payment_terms_days != pay_timing:
            said.append(
                f"QuickBooks gives Icon {paid_to.payment_terms_days} day(s) to pay {payee}"
                f" and the engagement list says {pay_timing}. I am using QuickBooks'"
                " terms. Make them agree."
            )
        pay_timing = paid_to.payment_terms_days
    return EngagementSnapshot(
        bill_rate_cents=bill_rate_cents,
        pay_rate_cents=pay_rate_cents,
        payment_terms_days=payment_terms_days,
        pay_timing_days=pay_timing,
        billing_emails=billing_emails,
        cc_emails=list(client.cc_emails),
        payee=payee,
        paid_by=paid_by.value,
        engagement_row_number=rate_row.row_number,
        role=rate_row.role,
        client_legal_name=legal_name,
        quickbooks_customer=client.quickbooks_customer,
        client_invoice_code=client.invoice_code,
        consultant_code=_consultant_code(workbook, rate_row),
        client_delivery=client.delivery.value,
        send_automatically=rate_row.send_automatically,
        rate_disagreement=" ".join(said),
        engagement_ref=engagement_ref,
    )


def _rates_from_accounting(
    accounting: "AccountingSystem | None",
    rate_row: Engagement,
    client: Client,
    failures: list[AccountingFailed] | None = None,
) -> "EngagementRates | None":
    """Ask the accounting system, and carry on without it when it cannot say.

    An accounting system that is down must not stop timesheets being read: the
    engagement list still has a rate, and the invoice is made on a later run
    anyway (decision 38).
    """
    if accounting is None:
        return None
    names = [
        name
        for name in dict.fromkeys([rate_row.client, client.quickbooks_customer, client.legal_name])
        if name
    ]
    try:
        return accounting.engagement_rates(rate_row.consultant, names)
    except AccountingFailed as error:
        if failures is not None:
            failures.append(error)
        logs.log(
            "could not ask the accounting system about the rates",
            consultant=rate_row.consultant,
            client=rate_row.client,
            said=str(error)[:200],
        )
        return None


def _party_from_accounting(
    accounting: "AccountingSystem | None",
    which: str,
    lookup: str,
    failures: list[AccountingFailed] | None = None,
) -> "AccountingParty | None":
    """A customer or payee record, or None where the accounting system cannot
    say. Like the rates, a record QuickBooks cannot find or cannot serve right
    now leaves the engagement list in charge rather than stopping the run: the
    invoice itself is what refuses a customer that does not exist."""
    if accounting is None or not lookup:
        return None
    try:
        return accounting.customer(lookup) if which == "customer" else accounting.payee(lookup)
    except AccountingFailed as error:
        if failures is not None:
            failures.append(error)
        logs.log(
            "could not ask the accounting system about a contact",
            which=which,
            said=str(error)[:200],
        )
        return None


def _find_item(
    deps: RunDeps,
    workbook: EngagementWorkbook,
    rate_row: Engagement | None,
    consultant: str,
    client: str,
    period: BillingPeriod,
) -> Item | None:
    """This engagement's item for this period, by name first and by the
    accounting system's id when the name finds nothing.

    A client or consultant renamed in the accounting system is the same
    engagement, and its id says so; looking only by name would have made a
    second item and expected a second invoice (docs/decisions.md #39). Where
    the item is found by id under different names, the names catch up.

    The name is tried first because it is a question for the agent's own
    store: an item found that way needs nothing from QuickBooks, and asking
    QuickBooks for every engagement's id on every run only to confirm an item
    already in hand was most of what a quiet run did (decision 54). Only a
    name the store does not know can be a rename.
    """
    found = deps.store.find_item(consultant, client, period)
    if found is not None or rate_row is None:
        return found
    client_row = _client_by_name(workbook, rate_row.client)
    if client_row is None:
        return None
    rates = _rates_from_accounting(deps.accounting, rate_row, client_row)
    if rates is None or not rates.ref:
        return None
    found = deps.store.find_item_by_engagement(rates.ref, period)
    if found is None:
        return None
    if (found.consultant, found.client) != (consultant, client):
        logs.log(
            "an engagement was renamed; catching the item up",
            item_id=found.id,
            was=f"{found.consultant} at {found.client}",
            now=f"{consultant} at {client}",
        )
        return deps.store.relabel_item(found.id, consultant, client)
    return found


def _ask_about_the_rates(deps: RunDeps, item: Item, report: RunReport) -> None:
    """QuickBooks and the engagement list disagree about a rate, or about who
    Icon pays.

    QuickBooks' figure is the one used, because that is where the rates live
    now (decisions 38 and 43), but Kevin is the one who pays and who signs off
    what a client is charged, and he is told before either happens.

    This **pauses the item** until he answers, as every review does. For the
    bill rate that is plainly right -- nothing should be invoiced at a price
    two systems disagree about. For the pay rate it means a client's invoice
    waits on a disagreement that does not affect it, which is the cost of one
    mechanism rather than two. Either way it is meant to be rare: `fops doctor`
    compares the two before any timesheet arrives, so the disagreements are
    found when someone is looking at the engagement list rather than when an
    invoice is due.
    """
    if not item.snapshot.rate_disagreement:
        return
    if deps.store.open_review(
        item.id, ReviewCode.LIST_ROW_PROBLEM.value, item.snapshot.rate_disagreement
    ):
        report.reviews_opened += 1
        email = emails.needs_review(
            deps.settings.admin_email,
            f"{item.consultant} — {item.client}",
            [item.snapshot.rate_disagreement],
        )
        outgoing_steps.enqueue_email(deps, "review_email", f"review:pay:{item.id}", item.id, email)


def _which_engagements_are_live(
    deps: RunDeps, workbook: EngagementWorkbook, report: RunReport
) -> tuple[LiveEngagements, bool]:
    """Ask the accounting system which engagements are live, and join.

    QuickBooks holds the engagements now, so it is what says one has finished:
    Kevin makes the product inactive and the agent stops expecting timesheets
    (decision 42). The schedule still comes off the workbook row, which is why
    an engagement QuickBooks has and the list does not is a review rather than
    a guess.

    An accounting system that cannot answer does not stop the run, for the same
    reason as the rates (decision 38): the engagement list still says which
    engagements are active, the timesheets are still read, and the next run
    picks the answer up. Being unable to ask must never look like Icon having
    stopped working.
    """
    if deps.settings.engagements_from == "quickbooks":
        # Built from QuickBooks' live products in the first place, so every
        # engagement here is live and every live product that could not be
        # built has already been reported with the reason.
        live = LiveEngagements(
            live=list(dict.fromkeys((row.consultant, row.client) for row in workbook.engagements))
        )
        return live, True
    listed: list[tuple[str, str]] = []
    asked = True
    try:
        listed = [
            (engagement.consultant, engagement.client)
            for engagement in deps.accounting.engagements().live
        ]
    except AccountingFailed as error:
        asked = False
        logs.log(
            "could not ask the accounting system which engagements are live", said=str(error)[:200]
        )
    answer = live_engagements(workbook, listed)
    for described in answer.without_a_row:
        _open_review(
            deps,
            report,
            None,
            Finding(
                ReviewCode.LIST_ROW_PROBLEM,
                f"QuickBooks has an engagement for {described} and the engagement list has"
                " no row for it, so I do not know how often to expect a timesheet or when"
                " the periods end. Add the row, or make the product inactive in QuickBooks"
                " if the engagement has finished.",
            ),
        )
    for consultant, client in answer.finished:
        # Not a review: `fops doctor` fails its products check on exactly this,
        # loudly and before any timesheet is due. Saying it twice would train
        # Kevin to skim both.
        logs.log("no longer live in the accounting system", consultant=consultant, client=client)
        report.note(f"no new periods expected: {consultant} at {client} is not live in QuickBooks")
    return answer, asked


def _create_expected_items(deps: RunDeps, workbook: EngagementWorkbook, report: RunReport) -> bool:
    """A billing period that has ended for a live engagement, with no timesheet
    yet, becomes a waiting_for_timesheet item.

    Says whether the accounting system could be asked which engagements are
    live, so the caller knows whether today's look is really done."""
    today = deps.clock.today()
    pairs = _engagement_pairs(workbook)
    live, asked = _which_engagements_are_live(deps, workbook, report)
    for consultant, client in live.live:
        rows = pairs[(consultant, client)]
        start, end, latest = _pair_window(rows)
        for period in billing_periods(
            latest.billing_schedule, start, end, latest.first_period_start, until=today
        ):
            if period.end >= today:
                continue
            rate_row, findings = checks.rate_row_in_force(rows, period)
            if rate_row is None or findings:
                continue  # the rate problem surfaces when a timesheet arrives
            # By the engagement's id first: a renamed one already has an item,
            # and looking only by name would expect a second invoice for work
            # that is already in hand (decision 39).
            if _find_item(deps, workbook, rate_row, consultant, client, period) is not None:
                continue
            snapshot = build_snapshot(workbook, rate_row, deps.accounting)
            if snapshot is None:
                continue
            waiting = deps.store.create_item(
                consultant,
                client,
                period,
                ItemStatus.WAITING_FOR_TIMESHEET,
                snapshot,
                snapshot.engagement_ref,
            )
            _ask_about_the_rates(deps, waiting, report)
            report.expected_items_created += 1
            report.note(
                f"waiting for a timesheet: {consultant} at {client}, {period.start} to {period.end}"
            )
    return asked


def _kind_of(deps: RunDeps, engagements: Engagements, sender: str) -> MessageKind:
    """What an email is, taking a fresh copy of the engagements once if the
    sender is not in the one in hand: a new consultant, or one whose address
    changed, looks exactly like that (decision 55)."""
    kind = decide_kind(deps, engagements.workbook, sender)
    if kind is MessageKind.UNKNOWN_SENDER and engagements.refresh_on_miss(
        "an email from an address not in my copy"
    ):
        kind = decide_kind(deps, engagements.workbook, sender)
    return kind


def _ingest_mailbox(
    deps: RunDeps,
    engagements: Engagements,
    emails: list[InboundEmail],
    new_position: str,
    report: RunReport,
) -> None:
    for email in emails:
        files: dict[str, bytes] = {}
        stored_attachments: list[StoredAttachment] = []
        for attachment in email.attachments:
            content = deps.inbox.download_attachment(email.message_id, attachment.attachment_id)
            sha256 = hashlib.sha256(content).hexdigest()
            files[sha256] = content
            stored_attachments.append(
                StoredAttachment(
                    filename=attachment.filename,
                    mime_type=attachment.mime_type,
                    sha256=sha256,
                    size_bytes=len(content),
                )
            )
        stored = StoredMessage(
            message_id=email.message_id,
            in_reply_to=email.in_reply_to,
            references=email.references,
            from_address=email.from_address,
            to_addresses=email.to_addresses,
            subject=email.subject,
            body_text=email.body_text,
            received_at=email.received_at,
            kind=_kind_of(deps, engagements, email.from_address),
            processed=False,
            attachments=tuple(stored_attachments),
        )
        if deps.store.record_message(stored, files):
            report.messages_stored += 1
    # Only now, with every fetched message stored, is the position moved.
    deps.store.set_state(MAILBOX_POSITION_KEY, new_position)


def _process_messages(deps: RunDeps, engagements: Engagements, report: RunReport) -> None:
    for message in deps.store.unprocessed_messages():
        if message.kind is MessageKind.UNKNOWN_SENDER:
            finding = Finding(
                ReviewCode.UNKNOWN_SENDER,
                f"This came from an address I don't recognise: {message.from_address}"
                f' ("{message.subject}").',
            )
            _open_review(deps, report, None, finding)
            # Kevin is emailed when it could be a timesheet; the rest wait in
            # the Monday summary, as newsletters always have.
            set_aside.set_aside(
                deps,
                message,
                finding.code,
                finding.message,
                email_kevin=bool(message.attachments),
            )
            deps.inbox.move(message.message_id, NEEDS_REVIEW_FOLDER)
            report.unknown_senders += 1
        elif message.kind is MessageKind.TIMESHEET:
            folder = _process_timesheet(deps, engagements, message, report)
            deps.inbox.move(message.message_id, folder)
        elif message.kind is MessageKind.KEVIN_REPLY:
            reply_steps.handle_kevin_reply(deps, message, report)
            deps.inbox.move(message.message_id, PROCESSED_FOLDER)
        else:
            # Client replies are recorded and listed in the Monday summary
            # (forwarding them unchanged is later work).
            deps.inbox.move(message.message_id, PROCESSED_FOLDER)
        deps.store.mark_processed(message.message_id)


def _reading_content_key(reading: TimesheetReading) -> str:
    """What makes two timesheets "the same": dates, hours, and approval."""
    approval = reading.approval.value
    return "|".join(
        [
            str(reading.period_start.value),
            str(reading.period_end.value),
            str(reading.stated_total_hours_hundredths.value),
            ",".join(
                f"{entry.day}:{entry.hours_hundredths}"
                for entry in (reading.daily_entries.value or [])
            ),
            approval.kind.value if approval else "none",
            (approval.approver or "") if approval else "",
        ]
    )


def _span(reading: TimesheetReading) -> tuple[date, date] | None:
    start, end = reading.period_start.value, reading.period_end.value
    if start is None or end is None or end < start:
        return None
    return start, end


def _stored_reading(record: TimesheetRecord) -> TimesheetReading:
    return TimesheetReading.model_validate(record.reading)


def _to_needs_review(deps: RunDeps, item: Item) -> None:
    if item.status is not ItemStatus.NEEDS_REVIEW:
        deps.store.change_status(item.id, ItemStatus.NEEDS_REVIEW, {})


@dataclass(frozen=True)
class _Placed:
    """Whose timesheet, for which client, which period, and at what rate row."""

    consultant: Consultant | None
    client_name: str | None
    period: BillingPeriod | None
    rate_row: Engagement | None
    findings: list[Finding]


def _place(
    deps: RunDeps, workbook: EngagementWorkbook, message: StoredMessage, reading: TimesheetReading
) -> _Placed:
    findings: list[Finding] = []
    named = set_aside.sender_is(deps, message.message_id)
    consultant = _consultant_by_name(workbook, named) if named else None
    if consultant is None:
        # Kevin's "this is from ..." decides when it names someone the
        # engagements have; otherwise the sender and the document do.
        consultant, consultant_findings = checks.match_consultant(
            message.from_address, reading, workbook.consultants
        )
        findings.extend(consultant_findings)

    client_name: str | None = None
    period: BillingPeriod | None = None
    rate_row: Engagement | None = None
    pinned, ruled_out = set_aside.client_is(deps, message.message_id)
    if consultant is not None and pinned:
        # Kevin said which client (decision 57): it decides, as long as the
        # consultant has an engagement there for it to be billed under.
        has_one = any(
            checks.names_match(row.consultant, consultant.name)
            and checks.names_match(row.client, pinned)
            for row in workbook.engagements
        )
        if has_one:
            client_name = next(
                row.client for row in workbook.engagements if checks.names_match(row.client, pinned)
            )
        else:
            findings.append(
                Finding(
                    ReviewCode.ENGAGEMENT_UNCLEAR,
                    f"You said this is for {pinned}, but {consultant.name} has no"
                    f" engagement there.",
                )
            )
    elif consultant is not None:
        client_names = {
            client.name: [client.name, client.legal_name, *client.names_on_timesheets]
            for client in workbook.clients
        }
        client_name, engagement_findings = checks.match_engagement(
            consultant, reading, workbook.engagements, client_names
        )
        findings.extend(engagement_findings)
        if client_name is not None and ruled_out and checks.names_match(client_name, ruled_out):
            # Kevin said it is not this one, and nothing else covers the dates.
            findings.append(
                Finding(
                    ReviewCode.ENGAGEMENT_UNCLEAR,
                    f"You said this is not for {ruled_out}, and {consultant.name} has no"
                    " other engagement for these dates.",
                )
            )
            client_name = None
    if consultant is not None and client_name is not None:
        rows = [
            row
            for row in workbook.engagements
            if checks.names_match(row.consultant, consultant.name)
            and checks.names_match(row.client, client_name)
        ]
        _, _, latest = _pair_window(rows)
        period, period_findings = checks.fit_billing_period(latest, reading)
        findings.extend(period_findings)
        if period is not None:
            rate_row, rate_findings = checks.rate_row_in_force(rows, period)
            findings.extend(rate_findings)
        client = _client_by_name(workbook, client_name)
        if client is not None and client.delivery is Delivery.EMAIL and not client.billing_emails:
            findings.append(
                Finding(
                    ReviewCode.NO_BILLING_CONTACT,
                    "The engagement list has no billing email for this client.",
                )
            )
    return _Placed(consultant, client_name, period, rate_row, findings)


# Statuses whose amounts are not yet worked out, so the rates can still change.
_STILL_PRICEABLE = (
    ItemStatus.WAITING_FOR_TIMESHEET,
    ItemStatus.RECEIVED,
    ItemStatus.NEEDS_REVIEW,
)
RATES_UNCONFIRMED = "I couldn't check the rates and billing details in QuickBooks"


def _unconfirmed_finding(item: Item, failures: list[AccountingFailed]) -> Finding:
    """QuickBooks could not be asked for the rates of the engagement in hand.

    The timesheet is still read and kept (decision 38); what waits is the
    invoice, which would otherwise go out on figures nobody confirmed today.
    Nothing is needed from Kevin unless QuickBooks needs reconnecting: every
    run asks again, and the item carries on by itself once it answers."""
    reconnect = any(isinstance(error, AccountingNeedsReconnect) for error in failures)
    code = ReviewCode.QUICKBOOKS_RECONNECT if reconnect else ReviewCode.QUICKBOOKS_FAILED
    return Finding(
        code,
        f"{RATES_UNCONFIRMED} for {item.consultant} at {item.client}, so I won't"
        " invoice this yet. I try again on every run and carry on by myself once"
        " QuickBooks answers."
        + (" Run `fops qbo-connect` to reconnect it." if reconnect else "")
        + ' Reply "ignore" to drop this timesheet instead.'
        + f" QuickBooks said: {str(failures[0])[:200]}",
    )


def _retry_unconfirmed(deps: RunDeps, engagements: Engagements, report: RunReport) -> None:
    """Ask QuickBooks again about the items whose rates it could not confirm.

    Only those items, and only while their review is open: this is the one
    place a quiet run may ask QuickBooks anything, and only because a timesheet
    is waiting on the answer."""
    waiting = [
        review
        for review in deps.store.open_reviews()
        if review.item_id is not None and review.message.startswith(RATES_UNCONFIRMED)
    ]
    if not waiting or report.quickbooks_unavailable:
        return
    pairs = _engagement_pairs(engagements.workbook)
    for review in waiting:
        assert review.item_id is not None
        item = deps.store.get_item(review.item_id)
        rows = next(
            (
                found
                for (consultant, client), found in pairs.items()
                if checks.names_match(consultant, item.consultant)
                and checks.names_match(client, item.client)
            ),
            [],
        )
        rate_row, problems = checks.rate_row_in_force(rows, item.period) if rows else (None, [])
        if rate_row is None or problems or item.status not in _STILL_PRICEABLE:
            continue  # nothing to price it against; the review stays for Kevin
        failures: list[AccountingFailed] = []
        snapshot = build_snapshot(engagements.workbook, rate_row, deps.accounting, failures)
        if snapshot is None or failures:
            continue
        item = deps.store.replace_snapshot(item.id, snapshot)
        deps.store.answer_review(
            review.id, {"kind": "resolved", "why": "QuickBooks answered"}, "answered"
        )
        report.note(f"QuickBooks confirmed the rates for {item.consultant} at {item.client}")
        _ask_about_the_rates(deps, item, report)
        complete_if_covered(deps, deps.store.get_item(item.id), report)


def _process_timesheet(
    deps: RunDeps, engagements: Engagements, message: StoredMessage, report: RunReport
) -> str:
    """Handle one timesheet email; returns the mailbox folder it is filed in
    afterwards (a courtesy for anyone looking at the mailbox; the database is
    the record)."""
    workbook = engagements.workbook
    key = set_aside.message_key(deps, message.message_id)
    if not message.attachments:
        finding = Finding(
            ReviewCode.NO_ATTACHMENT,
            "This looks like a timesheet email but has no attachment I can use"
            f' ("{message.subject}").',
        )
        _open_review(deps, report, None, finding)
        _enqueue_review_email(deps, key, None, [finding], None, None)
        return NEEDS_REVIEW_FOLDER
    attachment = message.attachments[0]
    if all(deps.store.timesheet_seen(part.sha256) for part in message.attachments):
        report.duplicates_filed += 1
        report.note(f"duplicate filed quietly: {attachment.filename} from {message.from_address}")
        return IGNORED_FOLDER
    hints = ReadingHints(
        email_subject=message.subject,
        consultant_names=[consultant.name for consultant in workbook.consultants],
        client_names=[client.name for client in workbook.clients],
    )
    # Every attachment is read, not just the first: a consultant working
    # through their own firm sends the approved timesheet and the firm's
    # invoice for the same hours in one email (decision 24).
    readings: list[TimesheetReading] = []
    read_attachments: list[StoredAttachment] = []
    unreadable: list[str] = []
    for part in message.attachments:
        try:
            readings.append(
                deps.reader.read_timesheet(
                    deps.store.load_file(part.sha256), part.filename, part.mime_type, hints
                )
            )
        except CantReadAttachmentError as error:
            unreadable.append(part.filename)
            # Why it could not be read is the whole diagnosis, and it is the
            # reader's own words: never discard it.
            report.note(f"could not read {part.filename}: {error}")
            continue
        read_attachments.append(part)
    if not readings:
        names = ", ".join(unreadable) or attachment.filename
        finding = Finding(
            ReviewCode.CANT_READ_ATTACHMENT,
            f'I couldn\'t read the attachment {names} ("{message.subject}").',
        )
        _open_review(deps, report, None, finding)
        _enqueue_review_email(
            deps,
            key,
            None,
            [finding],
            None,
            EmailAttachment(attachment.filename, attachment.sha256),
        )
        return NEEDS_REVIEW_FOLDER
    # The attachment filed against the item, shown to Kevin and sent to the
    # client, is the timesheet -- the document that shows approval -- and not
    # whichever file the email happened to list first. `readings` and
    # `read_attachments` are built together, so the index picks out both.
    attachment = read_attachments[checks.leading_index(readings)]
    reading, combine_findings = checks.combine_readings(readings)
    report.timesheets_processed += 1

    findings: list[Finding] = list(combine_findings)
    placed = _place(deps, workbook, message, reading)
    if any(f.code.value in set_aside.PLACEABLE for f in placed.findings) and (
        engagements.refresh_on_miss("a timesheet I could not place in my copy")
    ):
        workbook = engagements.workbook
        placed = _place(deps, workbook, message, reading)
    consultant, client_name = placed.consultant, placed.client_name
    period, rate_row = placed.period, placed.rate_row
    findings.extend(placed.findings)

    hours_total, hours_findings = checks.check_hours(reading, period)
    findings.extend(hours_findings)
    findings.extend(checks.check_approval(reading))
    findings.extend(checks.check_confidence(reading))

    item: Item | None = None
    is_duplicate = False
    is_correction = False
    if consultant is not None and client_name is not None and period is not None:
        item = _find_item(deps, workbook, rate_row, consultant.name, client_name, period)
        unconfirmed: list[AccountingFailed] = []
        if item is not None and rate_row is not None and item.status in _STILL_PRICEABLE:
            # Made before this timesheet arrived -- when its period ended, or by
            # an earlier part of the period -- so its rates are QuickBooks' as
            # of then. They are taken again now (decision 43).
            snapshot = build_snapshot(workbook, rate_row, deps.accounting, unconfirmed)
            if snapshot is not None and not unconfirmed:
                item = deps.store.replace_snapshot(item.id, snapshot)
                _ask_about_the_rates(deps, item, report)
        if item is None and rate_row is not None:
            snapshot = build_snapshot(workbook, rate_row, deps.accounting, unconfirmed)
            if snapshot is not None:
                item = deps.store.create_item(
                    consultant.name,
                    client_name,
                    period,
                    ItemStatus.RECEIVED,
                    snapshot,
                    snapshot.engagement_ref,
                )
                _ask_about_the_rates(deps, item, report)
        if item is not None and item.status is ItemStatus.WAITING_FOR_TIMESHEET:
            item = deps.store.change_status(item.id, ItemStatus.RECEIVED, {})
        if item is not None and unconfirmed:
            findings.append(_unconfirmed_finding(item, unconfirmed))
        if item is not None:
            span = _span(reading)
            for record in deps.store.timesheets_for_item(item.id):
                earlier = _stored_reading(record)
                earlier_span = _span(earlier)
                if span is None or earlier_span is None or record.is_duplicate:
                    continue
                overlaps = span[0] <= earlier_span[1] and earlier_span[0] <= span[1]
                if not overlaps:
                    continue
                if _reading_content_key(reading) == _reading_content_key(earlier):
                    is_duplicate = True
                else:
                    is_correction = True
                break

    if is_duplicate:
        report.duplicates_filed += 1
        report.note(
            f"duplicate filed quietly: same dates, hours, and approval ({attachment.filename})"
        )
    if item is not None:
        deps.store.record_timesheet(
            TimesheetRecord(
                item_id=item.id,
                sha256=attachment.sha256,
                reading=reading.model_dump(mode="json"),
                model=deps.reader.model_name,
                prompt_version=deps.reader.prompt_version,
                is_duplicate=is_duplicate,
                is_correction=is_correction,
            )
        )
    if is_duplicate:
        return IGNORED_FOLDER

    if is_correction and item is not None:
        findings.append(
            Finding(
                ReviewCode.CORRECTION,
                "This looks like a corrected version of a timesheet I already handled.",
            )
        )

    item_id = item.id if item is not None else None
    for finding in findings:
        _open_review(deps, report, item_id, finding)
    if item is not None and findings:
        _to_needs_review(deps, item)

    summary = _timesheet_summary(reading, consultant, client_name, period, item)
    timesheet_attachment = EmailAttachment(attachment.filename, attachment.sha256)

    # Details for Kevin's records, every time a timesheet is read (Objective 2).
    next_step = (
        "I'll ask you to review " + ", ".join(sorted({f.code.value for f in findings}))
        if findings
        else "everything checks out; I'll prepare the invoice"
    )
    details = emails.timesheet_details(
        deps.settings.admin_email, summary, next_step, timesheet_attachment
    )
    outgoing_steps.enqueue_email(deps, "details_email", f"details:{key}", item_id, details)
    unplaced = [f for f in findings if f.code.value in set_aside.PLACEABLE]
    if item is None and unplaced:
        # Nothing to hang Kevin's answer on but the email itself, so it is set
        # aside, and his "try again" or "this is from ..." reads it again.
        set_aside.set_aside(
            deps,
            message,
            unplaced[0].code,
            unplaced[0].message,
            email_kevin=True,
            summary=summary,
            timesheet=timesheet_attachment,
            also=[(f.code.value, f.message) for f in findings if f is not unplaced[0]],
        )
        return NEEDS_REVIEW_FOLDER
    if findings:
        _enqueue_review_email(deps, key, item_id, findings, summary, timesheet_attachment)
        return NEEDS_REVIEW_FOLDER

    if item is None:
        return NEEDS_REVIEW_FOLDER
    complete_if_covered(deps, deps.store.get_item(item.id), report)
    return PROCESSED_FOLDER


def _timesheet_summary(
    reading: TimesheetReading,
    consultant: Consultant | None,
    client_name: str | None,
    period: BillingPeriod | None,
    item: Item | None,
) -> TimesheetSummary:
    total, _ = checks.check_hours(reading, period or (item.period if item else None))
    approval = reading.approval.value
    if approval is None or approval.kind.value == "none":
        approval_text = "no approval found"
    else:
        by = f" by {approval.approver}" if approval.approver else ""
        on = f" on {approval.approval_date}" if approval.approval_date else ""
        approval_text = f"{approval.kind.value.replace('_', ' ')}{by}{on}"
    from finance_ops_agent.domain.money import Hours

    return TimesheetSummary(
        consultant=consultant.name if consultant else (reading.consultant_name.value or "unclear"),
        client=client_name or reading.client_name.value or "unclear",
        period=period,
        total_hours=None if total is None else Hours(total),
        approval=approval_text,
        engagement_row=item.snapshot.engagement_row_number if item else None,
    )


def _enqueue_review_email(
    deps: RunDeps,
    message_id: str,
    item_id: int | None,
    findings: list[Finding],
    summary: TimesheetSummary | None,
    timesheet: EmailAttachment | None,
) -> None:
    about = (
        f"{summary.consultant} — {summary.client}"
        if summary is not None
        else "an email I couldn't handle"
    )
    email = emails.needs_review(
        deps.settings.admin_email,
        about,
        [finding.message for finding in findings],
        summary,
        timesheet,
    )
    outgoing_steps.enqueue_email(deps, "review_email", f"review:{message_id}", item_id, email)
