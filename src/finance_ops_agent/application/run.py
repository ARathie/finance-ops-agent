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
from datetime import date

from finance_ops_agent import logs
from finance_ops_agent.application import outgoing as outgoing_steps
from finance_ops_agent.application import paid_check
from finance_ops_agent.application import replies as reply_steps
from finance_ops_agent.application import summary as summary_steps
from finance_ops_agent.application.completion import complete_if_covered
from finance_ops_agent.application.context import Mode, RunDeps, RunReport, Settings
from finance_ops_agent.domain import checks, emails
from finance_ops_agent.domain.checks import Finding
from finance_ops_agent.domain.emails import EmailAttachment, TimesheetSummary
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
from finance_ops_agent.domain.periods import BillingPeriod, billing_periods
from finance_ops_agent.domain.reading import ReadingHints, TimesheetReading
from finance_ops_agent.domain.review import ReviewCode
from finance_ops_agent.domain.statuses import ItemStatus
from finance_ops_agent.ports.inbox import IGNORED_FOLDER, NEEDS_REVIEW_FOLDER, PROCESSED_FOLDER
from finance_ops_agent.ports.reader import CantReadAttachmentError

MAILBOX_POSITION_KEY = "mailbox_position"

__all__ = ["Mode", "RunDeps", "RunReport", "Settings", "run_once"]


def run_once(deps: RunDeps, report: RunReport | None = None) -> RunReport:
    report = report or RunReport()
    logs.log("run started", mode=deps.settings.mode.value)
    workbook = parse_workbook(deps.engagement_list.load())
    _report_list_problems(deps, workbook, report)
    _create_expected_items(deps, workbook, report)
    _ingest_mailbox(deps, workbook, report)
    _process_messages(deps, workbook, report)
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


def _report_list_problems(deps: RunDeps, workbook: EngagementWorkbook, report: RunReport) -> None:
    problems = [
        f"{problem.sheet} sheet, row {problem.row_number}: {problem.message}"
        for problem in workbook.problems
    ]
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
        role=rate_row.role,
        client_legal_name=client.legal_name,
        quickbooks_customer=client.quickbooks_customer,
        client_delivery=client.delivery.value,
        send_automatically=rate_row.send_automatically,
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
    # Someone forwarding a timesheet on a consultant's behalf (decision 25).
    # The sender says nothing about whose timesheet it is, so match_consultant
    # falls through to the name on the document, which is the point.
    if sender in (address.casefold() for address in deps.settings.timesheet_forwarders):
        return MessageKind.TIMESHEET
    domain = sender.rsplit("@", 1)[-1]
    for client in workbook.clients:
        addresses = [address.casefold() for address in client.billing_emails + client.cc_emails]
        if sender in addresses or domain in (d.casefold() for d in client.email_domains):
            return MessageKind.CLIENT_REPLY
    return MessageKind.UNKNOWN_SENDER


def _ingest_mailbox(deps: RunDeps, workbook: EngagementWorkbook, report: RunReport) -> None:
    position = deps.store.get_state(MAILBOX_POSITION_KEY)
    emails, new_position = deps.inbox.new_messages(position)
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
            kind=_decide_kind(deps, workbook, email),
            processed=False,
            attachments=tuple(stored_attachments),
        )
        if deps.store.record_message(stored, files):
            report.messages_stored += 1
    # Only now, with every fetched message stored, is the position moved.
    deps.store.set_state(MAILBOX_POSITION_KEY, new_position)


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
            deps.inbox.move(message.message_id, NEEDS_REVIEW_FOLDER)
            report.unknown_senders += 1
        elif message.kind is MessageKind.TIMESHEET:
            folder = _process_timesheet(deps, workbook, message, report)
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


def _process_timesheet(
    deps: RunDeps, workbook: EngagementWorkbook, message: StoredMessage, report: RunReport
) -> str:
    """Handle one timesheet email; returns the mailbox folder it is filed in
    afterwards (a courtesy for anyone looking at the mailbox; the database is
    the record)."""
    if not message.attachments:
        finding = Finding(
            ReviewCode.NO_ATTACHMENT,
            "This looks like a timesheet email but has no attachment I can use"
            f' ("{message.subject}").',
        )
        _open_review(deps, report, None, finding)
        _enqueue_review_email(deps, message.message_id, None, [finding], None, None)
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
            message.message_id,
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

    hours_total, hours_findings = checks.check_hours(reading, period)
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
    outgoing_steps.enqueue_email(
        deps, "details_email", f"details:{message.message_id}", item_id, details
    )
    if findings:
        _enqueue_review_email(
            deps, message.message_id, item_id, findings, summary, timesheet_attachment
        )
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
    total, _ = checks.check_hours(reading)
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
