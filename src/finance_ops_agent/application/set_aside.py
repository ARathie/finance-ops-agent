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

import hashlib
import json
import re
import secrets
from dataclasses import asdict, dataclass, field

from finance_ops_agent import logs
from finance_ops_agent.application import engagement_copy
from finance_ops_agent.application import outgoing as outgoing_steps
from finance_ops_agent.application.context import Mode, RunDeps, RunReport
from finance_ops_agent.application.engagement_copy import Engagements
from finance_ops_agent.application.senders import decide_kind
from finance_ops_agent.domain import checks, emails, setup
from finance_ops_agent.domain.emails import EmailAttachment, TimesheetSummary
from finance_ops_agent.domain.engagements import Consultant, EngagementWorkbook
from finance_ops_agent.domain.items import OutgoingRecord
from finance_ops_agent.domain.messages import MessageKind, StoredMessage
from finance_ops_agent.domain.reading import ReplyAnswerKind
from finance_ops_agent.domain.review import ReviewCode
from finance_ops_agent.ports.accounting import AccountingFailed

SET_ASIDE_KEY = "set_aside"
SENDER_IS_PREFIX = "sender_is:"  # + Message-ID: whose timesheet Kevin said it is
CLIENT_IS_PREFIX = "client_is:"  # + Message-ID: which client it is for, once set up
CLIENT_NOT_PREFIX = "client_not:"  # + Message-ID: the client Kevin said it is not for
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
    # Setting it up in QuickBooks (decision 56): what the form was filled in
    # with, the plan Kevin's answers made, the one-time code he must send back
    # to confirm it, and whether he has.
    prefill: dict[str, str] = field(default_factory=dict)
    setup: dict[str, object] = field(default_factory=dict)
    confirm_code: str = ""
    confirmed: bool = False
    # Kevin said "wrong client" about it (decision 57): never this one again.
    not_client: str = ""


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


def client_is(deps: RunDeps, message_id: str) -> tuple[str, str]:
    """The client Kevin's setup pinned this email to, and the client he said
    it is not for; either may be ""."""
    return (
        deps.store.get_state(CLIENT_IS_PREFIX + message_id) or "",
        deps.store.get_state(CLIENT_NOT_PREFIX + message_id) or "",
    )


def sender_is(deps: RunDeps, message_id: str) -> str:
    """The consultant Kevin said this email is from, or ""."""
    return deps.store.get_state(SENDER_IS_PREFIX + message_id) or ""


# --- setting aside ---


def _can_set_up(deps: RunDeps) -> bool:
    """Only where the engagements live in QuickBooks: that is where a new one
    is made, and where the agent will look for it afterwards."""
    return deps.settings.engagements_from == "quickbooks"


def _setup_lines(prefill: dict[str, str]) -> list[str]:
    return [
        "To set it up, copy these lines into your reply and fill them in. I'll show",
        "you exactly what I'll create in QuickBooks, and change nothing until you",
        "confirm:",
        "",
        *setup.form(prefill),
    ]


# Where naming the consultant answers the question (decision 67 keeps "this is
# from ..." to these): the sender or the name on the page was not recognised.
NAMES_THE_CONSULTANT = (ReviewCode.UNKNOWN_SENDER.value, ReviewCode.CONSULTANT_UNKNOWN.value)


def _could_be_new(code: str) -> bool:
    """Whether the setup form could be the answer: someone or something the
    list does not have yet, not dates or a schedule that do not fit."""
    return code in PLACEABLE or code == ReviewCode.UNKNOWN_SENDER.value


def what_you_can_do(code: str, offer_setup: bool = False) -> list[str]:
    if code == ReviewCode.UNKNOWN_SENDER.value:
        where = (
            "Add the address to the consultant's vendor in QuickBooks (or their row in"
            " the engagement list)"
        )
    elif code in PLACEABLE:
        where = "Add or fix the consultant or client in QuickBooks (or the engagement list)"
    else:
        # The dates, the billing schedule, an end date or a rate: all in the list.
        where = (
            "Fix what is named above in QuickBooks or the engagement list -- the"
            " billing schedule, an end date, a rate"
        )
    lines = [f'{where}, then reply "try again" and I will handle the email again.']
    if code in NAMES_THE_CONSULTANT:
        # Naming the consultant only helps where the consultant is the question.
        lines.append(
            'Reply with who it is from, for example "this is from Priya Shah", and I'
            " will handle it as their timesheet."
        )
    offer_setup = offer_setup and _could_be_new(code)
    if offer_setup:
        lines.append(
            "If this is a new consultant, client or engagement -- or one that was"
            " never put in QuickBooks -- I can set it up for you: fill in the form"
            " below."
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
    prefill_extra: dict[str, str] | None = None,
    not_client: str = "",
) -> None:
    """Remember the email and, where it is worth his time, ask Kevin about it.

    The review itself is opened by the caller, as every review is."""
    prefill: dict[str, str] = {}
    forwarders = {address.casefold() for address in deps.settings.timesheet_forwarders}
    if message.from_address.casefold() not in forwarders:
        prefill[setup.CONSULTANT_EMAIL] = message.from_address
    if summary is not None and summary.consultant != "unclear":
        prefill[setup.CONSULTANT] = summary.consultant
    if summary is not None and summary.client != "unclear":
        prefill[setup.CLIENT] = summary.client
    prefill.update(prefill_extra or {})
    if not_client:
        deps.store.set_state(CLIENT_NOT_PREFIX + message.message_id, not_client)
    entries = [entry for entry in _load(deps) if entry.message_id != message.message_id]
    entries.append(
        SetAside(
            message_id=message.message_id,
            code=code.value,
            review_message=review_message,
            sender=message.from_address,
            subject=message.subject,
            also=[[other_code, other] for other_code, other in also or []],
            prefill=prefill,
            not_client=not_client,
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
        what_you_can_do=what_you_can_do(code.value, _can_set_up(deps)),
        then=_setup_lines(prefill) if _can_set_up(deps) and _could_be_new(code.value) else None,
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
    deps: RunDeps,
    record: OutgoingRecord,
    reply: StoredMessage,
    entry: SetAside,
    why: str,
    form_values: dict[str, str] | None = None,
) -> None:
    lines = [why, "", "What you can do:"]
    lines += [f"- {line}" for line in what_you_can_do(entry.code, _can_set_up(deps))]
    if _can_set_up(deps):
        lines += ["", *_setup_lines(form_values if form_values is not None else entry.prefill)]
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
    if record.payload.get("setup_confirm"):
        _handle_confirmation(deps, reply, record, body, entry, report)
        _save(deps, entries)
        return
    if setup.looks_like_a_setup(body):
        _handle_setup_form(deps, reply, record, body, entry, report)
        _save(deps, entries)
        return
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


# --- setting it up in QuickBooks (decision 56) ---


def _handle_setup_form(
    deps: RunDeps,
    reply: StoredMessage,
    record: OutgoingRecord,
    body: str,
    entry: SetAside,
    report: RunReport,
) -> None:
    """Kevin filled in the form. Code reads it -- never the model, because the
    rates are on it -- and checks it against the engagements as last seen.
    A good plan is put back to him to confirm; nothing is created yet."""
    if not _can_set_up(deps):
        _ask_again(
            deps,
            record,
            reply,
            entry,
            "I can only set things up in QuickBooks when I take the engagements from"
            " QuickBooks (FOPS_ENGAGEMENTS=quickbooks). Add it to the engagement list"
            ' instead, then reply "try again".',
        )
        return
    workbook = engagement_copy.stored_copy(deps)
    if workbook is None:
        workbook = EngagementWorkbook(
            clients=[], consultants=[], vendors=[], engagements=[], problems=[]
        )
    plan, problems, given = setup.read_setup(body, workbook)
    if plan is None:
        _ask_again(
            deps,
            record,
            reply,
            entry,
            "I couldn't set this up yet:\n" + "\n".join(f"- {p}" for p in problems),
            form_values=given,
        )
        report.note(f"Kevin's setup for {entry.sender} needs fixing: {len(problems)} problem(s)")
        return
    entry.setup = plan.to_dict()
    entry.confirm_code = f"{1000 + secrets.randbelow(9000)}"
    entry.confirmed = False
    lines = [
        "Before I change anything in QuickBooks, please check this:",
        "",
        *[f"- {line}" for line in plan.summary_lines()],
        "",
        f'Reply "confirm {entry.confirm_code}" to set it up. The number is there so'
        " that only a reply to this email can do it.",
        "To change anything, reply to the earlier email with the corrected form.",
        'Reply "cancel" to leave QuickBooks as it is.',
        "",
        "Once it is set up I will handle the email that started this.",
    ]
    ask = emails.OutgoingEmail(
        to=(deps.settings.admin_email,),
        subject=f"Set up in QuickBooks? {plan.consultant} at {plan.client}",
        in_reply_to=reply.message_id,
        body="\n".join(lines),
    )
    outgoing_steps.enqueue_email(
        deps,
        "setup_request",
        f"setup-request:{entry.message_id}:{entry.confirm_code}",
        None,
        ask,
        extra={"set_aside": entry.message_id, "setup_confirm": True},
    )
    report.note(f"asking Kevin to confirm setting up {plan.consultant} at {plan.client}")


def _handle_confirmation(
    deps: RunDeps,
    reply: StoredMessage,
    record: OutgoingRecord,
    body: str,
    entry: SetAside,
    report: RunReport,
) -> None:
    """Checked by code, like approving an invoice: the first word, and the
    one-time code from the email being answered."""
    words = re.findall(r"[A-Za-z]+|\d+", body)
    first = words[0].casefold() if words else ""
    if not entry.setup or not entry.confirm_code:
        return  # already done or cancelled
    if first == "cancel":
        entry.setup, entry.confirm_code, entry.confirmed = {}, "", False
        report.note(f"Kevin cancelled setting up the email from {entry.sender}")
        return
    if entry.confirmed:
        # Already confirmed and being retried on every run: "try again" after
        # a failure needs nothing more from him.
        report.note(f"setting up the email from {entry.sender} is already being retried")
        return
    if first == "confirm" and entry.confirm_code in words[1:3]:
        entry.confirmed = True
        report.note(f"Kevin confirmed setting up the email from {entry.sender}")
        return
    ask = emails.OutgoingEmail(
        to=(deps.settings.admin_email,),
        subject=f"Re: {record.payload.get('subject', '')}",
        in_reply_to=reply.message_id,
        body=(
            "Nothing has been changed in QuickBooks. To set it up, reply to my"
            ' email with "confirm" and the number it gives, exactly as written there;'
            ' or reply "cancel".'
        ),
    )
    outgoing_steps.enqueue_email(
        deps,
        "ask_again_email",
        f"askagain:{reply.message_id}",
        None,
        ask,
        extra={"set_aside": entry.message_id, "setup_confirm": True},
    )


def _set_up(deps: RunDeps, engagements: Engagements, entry: SetAside, report: RunReport) -> bool:
    """Make what Kevin confirmed. Returns whether it is done, so the email can
    be handled again against a copy that has it.

    Written in the outgoing table before QuickBooks is touched (CLAUDE.md
    rule 4). The adapter finds before it creates, so a crash or a failure
    part-way is finished by the next attempt rather than duplicated."""
    plan = setup.EngagementSetup.from_dict(entry.setup)
    who = f"{plan.consultant} at {plan.client}"
    key = f"setup:{entry.message_id}:{entry.confirm_code}"
    if deps.settings.mode is Mode.DRY_RUN:
        # Dry run creates nothing anywhere: it is the stop button.
        email = emails.OutgoingEmail(
            to=(deps.settings.admin_email,),
            subject=f"Dry run — would set up in QuickBooks: {who}",
            body="\n".join(
                [
                    "I'm in dry run, so I have changed nothing. Outside dry run I would"
                    " have set up:",
                    "",
                    *[f"- {line}" for line in plan.summary_lines()],
                ]
            ),
        )
        outgoing_steps.enqueue_email(deps, "preview_email", f"{key}:dry-run", None, email)
        entry.setup, entry.confirm_code, entry.confirmed = {}, "", False
        return False
    deps.store.record_outgoing("quickbooks_setup", key, None, plan.to_dict())
    try:
        done = deps.accounting.set_up_engagement(plan)
    except AccountingFailed as error:
        said = str(error)[:300]
        deps.store.update_outgoing(key, error=said)
        logs.log("could not set an engagement up in quickbooks", said=said)
        digest = hashlib.sha256(said.encode()).hexdigest()[:12]
        email = emails.OutgoingEmail(
            to=(deps.settings.admin_email,),
            subject=f"Couldn't finish setting up in QuickBooks: {who}",
            body="\n".join(
                [
                    f"QuickBooks said: {said}",
                    "",
                    "Anything already made stays as it is; I try again on every run and"
                    " finish the rest once QuickBooks accepts it.",
                    "",
                    "What you can do:",
                    '- Fix what QuickBooks named, then wait, or reply "try again".',
                    '- Reply "cancel" to stop trying.',
                ]
            ),
        )
        outgoing_steps.enqueue_email(
            deps,
            "setup_request",
            f"{key}:failed:{digest}",
            None,
            email,
            extra={"set_aside": entry.message_id, "setup_confirm": True},
        )
        report.note(f"could not set up {who} in QuickBooks: {said}")
        return False
    deps.store.update_outgoing(key, status="done")
    lines = [f"Done. In QuickBooks I set up {who}:", ""]
    lines += [f"- made {thing}" for thing in done.created]
    lines += [f"- used the existing {thing}" for thing in done.reused]
    lines += ["", "I'm handling the email that started this now."]
    email = emails.OutgoingEmail(
        to=(deps.settings.admin_email,),
        subject=f"Set up in QuickBooks: {who}",
        body="\n".join(lines),
    )
    outgoing_steps.enqueue_email(deps, "details_email", f"{key}:done", None, email)
    report.note(f"set up {who} in QuickBooks")
    engagements.refresh_now("Kevin's setup was just made")
    entry.consultant = plan.consultant
    # The email is for the engagement just made, whatever else covers its dates.
    deps.store.set_state(CLIENT_IS_PREFIX + entry.message_id, plan.client)
    return True


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
        if entry.confirmed and entry.setup:
            if not _set_up(deps, engagements, entry, report):
                keep.append(entry)
                continue
            workbook = engagements.workbook
        if (
            not entry.retry
            and not entry.consultant
            and not entry.setup
            and not _the_review(deps, entry)
        ):
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
        if entry.retry:
            # Read again from the start, against the fresh copy. If it still
            # cannot be placed, it is set aside again with a fresh question.
            # Whatever kept it from becoming an item -- the consultant, the
            # client, the dates, the rate -- is fixed in the list (decision 67).
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
    lines += [f"- {line}" for line in what_you_can_do(entry.code)]
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
