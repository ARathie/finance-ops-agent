"""One run of the agent, on ports only: ingest mail, check timesheets, keep records.

The order follows docs/technical-design.md. Never twice, at every step:
messages are stored before they are processed and marked processed only when
handling finished; the mailbox cursor is saved only after the run's messages
are stored; a second run over the same mailbox changes nothing.

This run stops at `ready` (dry run behaviour): composing and sending the
actual emails, and the modes, arrive with PR 7. What would be sent is already
written down in the outgoing table, once per thing, never twice.
"""

import hashlib
from dataclasses import dataclass, field
from datetime import date

from finance_ops_agent.domain import checks
from finance_ops_agent.domain.checks import Finding
from finance_ops_agent.domain.engagements import (
    Client,
    Consultant,
    ConsultantType,
    Delivery,
    Engagement,
    EngagementWorkbook,
    Vendor,
    parse_workbook,
)
from finance_ops_agent.domain.items import EngagementSnapshot, Item, TimesheetRecord
from finance_ops_agent.domain.messages import (
    InboundEmail,
    MessageKind,
    StoredAttachment,
    StoredMessage,
)
from finance_ops_agent.domain.money import Hours, Money, invoice_amount, pay_amount
from finance_ops_agent.domain.periods import BillingPeriod, billing_periods
from finance_ops_agent.domain.reading import TimesheetReading
from finance_ops_agent.domain.review import ReviewCode
from finance_ops_agent.domain.statuses import ItemStatus
from finance_ops_agent.ports.clock import Clock
from finance_ops_agent.ports.engagement_list import EngagementList
from finance_ops_agent.ports.inbox import NEEDS_REVIEW_FOLDER, EmailInbox
from finance_ops_agent.ports.reader import CantReadAttachmentError, TimesheetReader
from finance_ops_agent.ports.store import Store

MAILBOX_CURSOR_KEY = "mailbox_cursor"
READER_NAME = "fake"  # the Claude adapter (PR 6) records its real model name
PROMPT_VERSION = "0"


@dataclass(frozen=True)
class Settings:
    admin_email: str = "kevin@icon-technologies.com"


@dataclass
class RunDeps:
    engagement_list: EngagementList
    inbox: EmailInbox
    reader: TimesheetReader
    store: Store
    clock: Clock
    settings: Settings


@dataclass
class RunReport:
    messages_stored: int = 0
    timesheets_processed: int = 0
    duplicates_filed: int = 0
    reviews_opened: int = 0
    items_made_ready: int = 0
    expected_items_created: int = 0
    unknown_senders: int = 0
    lines: list[str] = field(default_factory=list)

    def note(self, line: str) -> None:
        self.lines.append(line)


def run_once(deps: RunDeps, report: RunReport | None = None) -> RunReport:
    report = report or RunReport()
    workbook = parse_workbook(deps.engagement_list.load())
    _report_list_problems(deps, workbook, report)
    _create_expected_items(deps, workbook, report)
    _ingest_mailbox(deps, workbook, report)
    _process_messages(deps, workbook, report)
    return report


def _open_review(deps: RunDeps, report: RunReport, item_id: int | None, finding: Finding) -> None:
    if deps.store.open_review(item_id, finding.code.value, finding.message):
        report.reviews_opened += 1
        report.note(f"needs Kevin's review ({finding.code.value}): {finding.message}")


def _report_list_problems(deps: RunDeps, workbook: EngagementWorkbook, report: RunReport) -> None:
    for problem in workbook.problems:
        finding = Finding(
            ReviewCode.LIST_ROW_PROBLEM,
            f"{problem.sheet} sheet, row {problem.row_number}: {problem.message}",
        )
        _open_review(deps, report, None, finding)


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


def _build_snapshot(
    workbook: EngagementWorkbook, rate_row: Engagement
) -> EngagementSnapshot | None:
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
    return EngagementSnapshot(
        bill_rate_cents=rate_row.bill_rate.cents,
        pay_rate_cents=rate_row.pay_rate.cents,
        payment_terms_days=client.payment_terms_days,
        pay_timing_days=pay_timing,
        billing_emails=list(client.billing_emails),
        cc_emails=list(client.cc_emails),
        payee=payee,
        paid_by=paid_by.value,
        engagement_row_number=rate_row.row_number,
    )


def _create_expected_items(deps: RunDeps, workbook: EngagementWorkbook, report: RunReport) -> None:
    """A billing period that has ended for an active engagement, with no
    timesheet yet, becomes a waiting_for_timesheet item."""
    today = deps.clock.today()
    for (consultant, client), rows in _engagement_pairs(workbook).items():
        if not any(row.active for row in rows):
            continue
        start, end, latest = _pair_window(rows)
        for period in billing_periods(
            latest.billing_schedule, start, end, latest.first_period_start, until=today
        ):
            if period.end >= today:
                continue
            if deps.store.find_item(consultant, client, period) is not None:
                continue
            rate_row, findings = checks.rate_row_in_force(rows, period)
            if rate_row is None or findings:
                continue  # the rate problem surfaces when a timesheet arrives
            snapshot = _build_snapshot(workbook, rate_row)
            if snapshot is None:
                continue
            deps.store.create_item(
                consultant, client, period, ItemStatus.WAITING_FOR_TIMESHEET, snapshot
            )
            report.expected_items_created += 1
            report.note(
                f"waiting for a timesheet: {consultant} at {client}, {period.start} to {period.end}"
            )


def _decide_kind(deps: RunDeps, workbook: EngagementWorkbook, email: InboundEmail) -> MessageKind:
    sender = email.from_address.strip().casefold()
    if not sender:
        return MessageKind.UNKNOWN_SENDER
    if sender == deps.settings.admin_email.casefold():
        return MessageKind.KEVIN_REPLY
    for consultant in workbook.consultants:
        if sender in (address.casefold() for address in consultant.emails):
            return MessageKind.TIMESHEET
    for vendor in workbook.vendors:
        if sender in (address.casefold() for address in vendor.contact_emails):
            return MessageKind.TIMESHEET
    domain = sender.rsplit("@", 1)[-1]
    for client in workbook.clients:
        addresses = [address.casefold() for address in client.billing_emails + client.cc_emails]
        if sender in addresses or domain in (d.casefold() for d in client.email_domains):
            return MessageKind.CLIENT_REPLY
    return MessageKind.UNKNOWN_SENDER


def _ingest_mailbox(deps: RunDeps, workbook: EngagementWorkbook, report: RunReport) -> None:
    cursor = deps.store.get_state(MAILBOX_CURSOR_KEY)
    emails, new_cursor = deps.inbox.new_messages(cursor)
    for email in emails:
        files: dict[str, bytes] = {}
        stored_attachments: list[StoredAttachment] = []
        for attachment in email.attachments:
            content = deps.inbox.download_attachment(email.provider_id, attachment.attachment_id)
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
            provider_id=email.provider_id,
            internet_message_id=email.internet_message_id,
            conversation_id=email.conversation_id,
            from_address=email.from_address,
            to_addresses=email.to_addresses,
            subject=email.subject,
            received_at=email.received_at,
            kind=_decide_kind(deps, workbook, email),
            processed=False,
            attachments=tuple(stored_attachments),
        )
        if deps.store.record_message(stored, files):
            report.messages_stored += 1
    # Only now, with every fetched message stored, is the cursor moved.
    deps.store.set_state(MAILBOX_CURSOR_KEY, new_cursor)


def _process_messages(deps: RunDeps, workbook: EngagementWorkbook, report: RunReport) -> None:
    for message in deps.store.unprocessed_messages():
        if message.kind is MessageKind.UNKNOWN_SENDER:
            _open_review(
                deps,
                report,
                None,
                Finding(
                    ReviewCode.UNKNOWN_SENDER,
                    f"This came from an address I don't recognise: {message.from_address}"
                    f' ("{message.subject}").',
                ),
            )
            deps.inbox.move(message.provider_id, NEEDS_REVIEW_FOLDER)
            report.unknown_senders += 1
        elif message.kind is MessageKind.TIMESHEET:
            _process_timesheet(deps, workbook, message, report)
        # Kevin's replies and client replies are handled with the emails in PR 7.
        deps.store.mark_processed(message.provider_id)


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


def _process_timesheet(
    deps: RunDeps, workbook: EngagementWorkbook, message: StoredMessage, report: RunReport
) -> None:
    if not message.attachments:
        _open_review(
            deps,
            report,
            None,
            Finding(
                ReviewCode.NO_ATTACHMENT,
                "This looks like a timesheet email but has no attachment I can use"
                f' ("{message.subject}").',
            ),
        )
        return
    attachment = message.attachments[0]
    if deps.store.timesheet_seen(attachment.sha256):
        report.duplicates_filed += 1
        report.note(f"duplicate filed quietly: {attachment.filename} from {message.from_address}")
        return
    content = deps.store.load_file(attachment.sha256)
    try:
        reading = deps.reader.read_timesheet(content, attachment.filename, attachment.mime_type)
    except CantReadAttachmentError:
        _open_review(
            deps,
            report,
            None,
            Finding(
                ReviewCode.CANT_READ_ATTACHMENT,
                f'I couldn\'t read the attachment {attachment.filename} ("{message.subject}").',
            ),
        )
        return
    report.timesheets_processed += 1

    findings: list[Finding] = []
    consultant, consultant_findings = checks.match_consultant(
        message.from_address, reading, workbook.consultants
    )
    findings.extend(consultant_findings)

    client_name: str | None = None
    period: BillingPeriod | None = None
    rate_row: Engagement | None = None
    if consultant is not None:
        client_names = {
            client.name: [client.name, client.legal_name, *client.names_on_timesheets]
            for client in workbook.clients
        }
        client_name, engagement_findings = checks.match_engagement(
            consultant, reading, workbook.engagements, client_names
        )
        findings.extend(engagement_findings)
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

    hours_total, hours_findings = checks.check_hours(reading)
    findings.extend(hours_findings)
    findings.extend(checks.check_approval(reading))
    findings.extend(checks.check_confidence(reading))

    item: Item | None = None
    is_duplicate = False
    is_correction = False
    if consultant is not None and client_name is not None and period is not None:
        item = deps.store.find_item(consultant.name, client_name, period)
        if item is None and rate_row is not None:
            snapshot = _build_snapshot(workbook, rate_row)
            if snapshot is not None:
                item = deps.store.create_item(
                    consultant.name, client_name, period, ItemStatus.RECEIVED, snapshot
                )
        if item is not None and item.status is ItemStatus.WAITING_FOR_TIMESHEET:
            item = deps.store.change_status(item.id, ItemStatus.RECEIVED, {})
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
                model=READER_NAME,
                prompt_version=PROMPT_VERSION,
                is_duplicate=is_duplicate,
                is_correction=is_correction,
            )
        )
    if is_duplicate:
        return

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

    # Details for Kevin's records, every time a timesheet is read (Objective 2).
    deps.store.record_outgoing(
        "details_to_kevin",
        f"details:{message.provider_id}",
        item_id,
        {
            "consultant": consultant.name if consultant else reading.consultant_name.value,
            "client": client_name or reading.client_name.value,
            "period_start": str(reading.period_start.value),
            "period_end": str(reading.period_end.value),
            "hours_hundredths": hours_total,
            "subject": message.subject,
        },
    )
    if findings:
        deps.store.record_outgoing(
            "review_to_kevin",
            f"review:{message.provider_id}",
            item_id,
            {"codes": sorted({finding.code.value for finding in findings})},
        )
        return

    if item is None or period is None:
        return
    _complete_if_covered(deps, item, period, report)


def _complete_if_covered(
    deps: RunDeps, item: Item, period: BillingPeriod, report: RunReport
) -> None:
    """Weekly timesheets for a monthly engagement wait here until the whole
    period is covered; then hours are summed and the item becomes ready."""
    if any(review.item_id == item.id for review in deps.store.open_reviews()):
        return  # something is still waiting on Kevin
    latest_by_span: dict[tuple[date, date], TimesheetReading] = {}
    for record in deps.store.timesheets_for_item(item.id):
        if record.is_duplicate or record.is_correction:
            continue
        reading = _stored_reading(record)
        span = _span(reading)
        if span is not None:
            latest_by_span[span] = reading
    if not checks.period_fully_covered(period, list(latest_by_span)):
        report.note(
            f"waiting for the rest of the period: {item.consultant} at {item.client},"
            f" {period.start} to {period.end}"
        )
        return
    total = Hours(0)
    for reading in latest_by_span.values():
        hours, _ = checks.check_hours(reading)
        total = total + Hours(hours or 0)
    billed = invoice_amount(total, Money(item.snapshot.bill_rate_cents))
    owed = pay_amount(total, Money(item.snapshot.pay_rate_cents))
    deps.store.set_item_amounts(item.id, total, billed, owed)
    deps.store.change_status(item.id, ItemStatus.READY, {"hours_hundredths": total.hundredths})
    report.items_made_ready += 1
    report.note(
        f"ready to invoice: {item.consultant} at {item.client},"
        f" {period.start} to {period.end}: {total} hours,"
        f" invoice {billed}, owed {owed}"
    )
