"""Emails set aside because the agent could not place them, and what Kevin can
say about them (decision 55).

Two kinds are set aside:

- an email from an address the agent does not recognise, even after taking a
  fresh copy of the engagements;
- a timesheet that names a consultant or client it cannot place, again after a
  fresh copy.

Each is remembered here, by its Message-ID, and Kevin is emailed with the
replies that work:

- **"try again"** -- he has added the address or the consultant in QuickBooks
  (or the engagement list). The agent takes a fresh copy and handles the email
  again.
- **"this is from Priya Shah"** -- the agent handles it as that consultant's
  timesheet, from now on as if it had come from her address.
- **"ignore"** -- the email stays in Needs Review and nothing more happens.

An address that becomes known in the engagements by itself (Kevin fixes it
without replying) is picked up on the next run, since checking an address costs
nothing. A timesheet that could not be placed is only read again when Kevin
asks: reading it costs a call to Claude, and doing it every day unasked would
email him the same question every day.
"""

import json
from dataclasses import asdict, dataclass, field

from finance_ops_agent import logs
from finance_ops_agent.application import engagement_copy
from finance_ops_agent.application import outgoing as outgoing_steps
from finance_ops_agent.application.context import RunDeps, RunReport
from finance_ops_agent.application.engagement_copy import Engagements
from finance_ops_agent.application.senders import decide_kind
from finance_ops_agent.domain import checks, emails
from finance_ops_agent.domain.emails import EmailAttachment, TimesheetSummary
from finance_ops_agent.domain.engagements import Consultant
from finance_ops_agent.domain.items import OutgoingRecord
from finance_ops_agent.domain.messages import MessageKind, StoredMessage
from finance_ops_agent.domain.reading import ReplyAnswerKind
from finance_ops_agent.domain.review import ReviewCode

SET_ASIDE_KEY = "set_aside"
SENDER_IS_PREFIX = "sender_is:"  # + Message-ID: whose timesheet Kevin said it is
ATTEMPT_PREFIX = "attempt:"  # + Message-ID: how many times it has been handled again

PLACEABLE = (ReviewCode.CONSULTANT_UNKNOWN.value, ReviewCode.ENGAGEMENT_UNCLEAR.value)


@dataclass
class SetAside:
    message_id: str
    code: str
    review_message: str
    sender: str
    subject: str
    retry: bool = False  # Kevin said "try again", or named the consultant
    consultant: str = ""  # who Kevin said it is from
    # The other questions the same email raised, as [code, message]: closed
    # with it when it is handled again, since handling it again asks afresh.
    also: list[list[str]] = field(default_factory=list)


def _load(deps: RunDeps) -> list[SetAside]:
    text = deps.store.get_state(SET_ASIDE_KEY)
    return [SetAside(**entry) for entry in json.loads(text)] if text else []


def _save(deps: RunDeps, entries: list[SetAside]) -> None:
    deps.store.set_state(SET_ASIDE_KEY, json.dumps([asdict(entry) for entry in entries]))


def message_key(deps: RunDeps, message_id: str) -> str:
    """The key an email's own emails are written down under.

    The same as its Message-ID the first time; handling it again after Kevin's
    answer is a new attempt, and its emails must not be mistaken for the ones
    already sent (CLAUDE.md rule 4 is about sending the same thing twice, not
    about never saying anything new)."""
    attempt = deps.store.get_state(ATTEMPT_PREFIX + message_id)
    return f"{message_id}#{attempt}" if attempt else message_id


def sender_is(deps: RunDeps, message_id: str) -> str:
    """The consultant Kevin said this email is from, or ""."""
    return deps.store.get_state(SENDER_IS_PREFIX + message_id) or ""


# --- setting aside ---


def _what_you_can_do(code: str) -> list[str]:
    if code == ReviewCode.UNKNOWN_SENDER.value:
        where = (
            "Add the address to the consultant's vendor in QuickBooks (or their row in"
            " the engagement list)"
        )
    else:
        where = "Add or fix the consultant or client in QuickBooks (or the engagement list)"
    lines = [f'{where}, then reply "try again" and I will handle the email again.']
    if code != ReviewCode.ENGAGEMENT_UNCLEAR.value:
        # Naming the consultant only helps where the consultant is the question.
        lines.append(
            'Reply with who it is from, for example "this is from Priya Shah", and I'
            " will handle it as their timesheet."
        )
    lines.append('Reply "ignore" if it is not a timesheet; it stays in Needs Review.')
    return lines


def set_aside(
    deps: RunDeps,
    message: StoredMessage,
    code: ReviewCode,
    review_message: str,
    email_kevin: bool,
    summary: TimesheetSummary | None = None,
    timesheet: EmailAttachment | None = None,
    also: list[tuple[str, str]] | None = None,
) -> None:
    """Remember the email and, where it is worth his time, ask Kevin about it.

    The review itself is opened by the caller, as every review is."""
    entries = [entry for entry in _load(deps) if entry.message_id != message.message_id]
    entries.append(
        SetAside(
            message_id=message.message_id,
            code=code.value,
            review_message=review_message,
            sender=message.from_address,
            subject=message.subject,
            also=[[other_code, other] for other_code, other in also or []],
        )
    )
    _save(deps, entries)
    if not email_kevin:
        return
    about = (
        f"{summary.consultant} — {summary.client}"
        if summary is not None
        else f"an email from {message.from_address}"
    )
    email = emails.needs_review(
        deps.settings.admin_email,
        about,
        [review_message, *(other for _code, other in also or [])],
        summary,
        timesheet,
        what_you_can_do=_what_you_can_do(code.value),
    )
    outgoing_steps.enqueue_email(
        deps,
        "review_email",
        f"review:{message_key(deps, message.message_id)}",
        None,
        email,
        extra={"set_aside": message.message_id},
    )


# --- Kevin's reply ---


def _the_review(deps: RunDeps, entry: SetAside) -> list[int]:
    """The open reviews this one email raised."""
    raised = {(entry.code, entry.review_message), *((code, text) for code, text in entry.also)}
    return [
        review.id
        for review in deps.store.open_reviews()
        if review.item_id is None and (review.code, review.message) in raised
    ]


def _ask_again(
    deps: RunDeps, record: OutgoingRecord, reply: StoredMessage, entry: SetAside, why: str
) -> None:
    lines = [why, "", "What you can do:"]
    lines += [f"- {line}" for line in _what_you_can_do(entry.code)]
    ask = emails.OutgoingEmail(
        to=(deps.settings.admin_email,),
        subject=f"Re: {record.payload.get('subject', '')}",
        in_reply_to=reply.message_id,
        body="\n".join(lines),
    )
    outgoing_steps.enqueue_email(
        deps,
        "ask_again_email",
        f"askagain:{reply.message_id}",
        None,
        ask,
        extra={"set_aside": entry.message_id},
    )


def handle_reply(
    deps: RunDeps, reply: StoredMessage, record: OutgoingRecord, body: str, report: RunReport
) -> None:
    """Kevin answered about one set-aside email. Only that email's question is
    put to the reader, so an answer about it can never close anything else."""
    message_id = str(record.payload.get("set_aside", ""))
    entries = _load(deps)
    entry = next((entry for entry in entries if entry.message_id == message_id), None)
    if entry is None:
        return  # already handled; nothing left to answer
    reading = deps.reader.read_reply(body, [(entry.code, entry.review_message)])
    kinds = {answer.kind for answer in reading.answers}
    named = next(
        (
            answer.value
            for answer in reading.answers
            if answer.kind is ReplyAnswerKind.CONSULTANT_NAME and answer.value
        ),
        "",
    )
    if named:
        entry.consultant, entry.retry = named, True
        report.note(f'Kevin said the email from {entry.sender} is from "{named}"')
    elif ReplyAnswerKind.TRY_AGAIN in kinds:
        entry.retry = True
        report.note(f"Kevin asked me to try the email from {entry.sender} again")
    elif ReplyAnswerKind.IGNORE in kinds:
        for review_id in _the_review(deps, entry):
            deps.store.answer_review(review_id, {"kind": "ignore"}, "ignored")
        entries = [other for other in entries if other.message_id != message_id]
        report.note(f"Kevin said ignore the email from {entry.sender}")
    else:
        _ask_again(
            deps,
            record,
            reply,
            entry,
            "Sorry - I couldn't tell what your reply means for this email.",
        )
        return
    if entry.retry:
        engagement_copy.request_refresh(deps)
    _save(deps, entries)


# --- looking again ---


def _consultant_named(consultants: list[Consultant], name: str) -> Consultant | None:
    matches = [
        consultant for consultant in consultants if checks.names_match(consultant.name, name)
    ]
    return matches[0] if len(matches) == 1 else None


def _requeue(deps: RunDeps, entry: SetAside, kind: MessageKind) -> None:
    for review_id in _the_review(deps, entry):
        deps.store.answer_review(review_id, {"kind": "resolved"}, "answered")
    attempt = int(deps.store.get_state(ATTEMPT_PREFIX + entry.message_id) or "0") + 1
    deps.store.set_state(ATTEMPT_PREFIX + entry.message_id, str(attempt))
    deps.store.requeue_message(entry.message_id, kind)
    logs.log("handling a set-aside email again", kind=kind.value)


def look_again(deps: RunDeps, engagements: Engagements, report: RunReport) -> bool:
    """Put back in the queue whatever can now be placed. Returns whether
    anything was, so the run knows to handle messages once more."""
    entries = _load(deps)
    if not entries:
        return False
    if any(entry.retry for entry in entries) and not engagements.refreshed:
        engagements.refresh_on_miss("Kevin asked to try a set-aside email again")
    workbook = engagements.workbook
    keep: list[SetAside] = []
    requeued = False
    for entry in entries:
        if not entry.retry and not _the_review(deps, entry):
            continue  # its question was closed some other way; nothing to look for
        if entry.consultant:
            consultant = _consultant_named(workbook.consultants, entry.consultant)
            if consultant is not None:
                deps.store.set_state(SENDER_IS_PREFIX + entry.message_id, consultant.name)
                _requeue(deps, entry, MessageKind.TIMESHEET)
                report.note(f"handling the email from {entry.sender} as {consultant.name}'s")
                requeued = True
                continue
            _tell_kevin_still_stuck(
                deps,
                entry,
                f'I couldn\'t find one consultant called "{entry.consultant}" in'
                f" {_where(engagements)}, so I still can't handle the email from"
                f' {entry.sender} ("{entry.subject}").',
            )
            entry.consultant, entry.retry = "", False
            keep.append(entry)
            continue
        if entry.code == ReviewCode.UNKNOWN_SENDER.value:
            kind = decide_kind(deps, workbook, entry.sender)
            if kind is not MessageKind.UNKNOWN_SENDER:
                _requeue(deps, entry, kind)
                report.note(f"I recognise {entry.sender} now; handling their email")
                requeued = True
                continue
            if entry.retry:
                _tell_kevin_still_stuck(
                    deps,
                    entry,
                    f"I looked again in {_where(engagements)} and still don't recognise"
                    f' {entry.sender} ("{entry.subject}").',
                )
                entry.retry = False
            keep.append(entry)
            continue
        if entry.code in PLACEABLE and entry.retry:
            # Read again from the start, against the fresh copy. If it still
            # cannot be placed, it is set aside again with a fresh question.
            _requeue(deps, entry, MessageKind.TIMESHEET)
            requeued = True
            continue
        keep.append(entry)
    _save(deps, keep)
    return requeued


def _where(engagements: Engagements) -> str:
    if not engagements.from_quickbooks:
        return "the engagement list"
    if engagements.refreshed:
        return "QuickBooks just now"
    return "my last copy of QuickBooks (QuickBooks could not be asked just now)"


def _tell_kevin_still_stuck(deps: RunDeps, entry: SetAside, why: str) -> None:
    attempt = deps.store.get_state(ATTEMPT_PREFIX + entry.message_id) or "0"
    lines = [why, "", "What you can do:"]
    lines += [f"- {line}" for line in _what_you_can_do(entry.code)]
    email = emails.OutgoingEmail(
        to=(deps.settings.admin_email,),
        subject=f"Still needs your review: the email from {entry.sender}",
        body="\n".join(lines),
    )
    count = len(
        [
            record
            for record in deps.store.outgoing_records()
            if record.payload.get("set_aside") == entry.message_id
        ]
    )
    outgoing_steps.enqueue_email(
        deps,
        "review_email",
        f"still-stuck:{entry.message_id}:{attempt}:{count}",
        None,
        email,
        extra={"set_aside": entry.message_id},
    )
