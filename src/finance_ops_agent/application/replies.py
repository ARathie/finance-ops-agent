"""Kevin's replies, in his own words: read by Claude, checked and applied by code.

Only replies from Kevin's address count (the run routes by sender). A reply is
matched to what it answers by its In-Reply-To (or References) header naming
the Message-ID the agent sent, and by the subject as a fallback (decision 17).

Kevin writes the way people write email, so a reply is not held to a fixed
form (decision 59). The model turns it into a list of typed requests, each with
the words it relied on; code checks every one -- is that number usable, is the
quote really in the reply, can this be done at this stage -- and does it or says
why not. Kevin always hears back what was done and what was not, in the same
thread. An approval that starts with "approve" or "cancel" is still taken as
it stands, without the model.
"""

import re
from dataclasses import dataclass, field

from finance_ops_agent.application import engagement_copy, outages, outgoing, set_aside
from finance_ops_agent.application.completion import complete_if_covered
from finance_ops_agent.application.context import Mode, RunDeps, RunReport
from finance_ops_agent.application.investigation import offered_replies, picked_option
from finance_ops_agent.domain import emails, setup
from finance_ops_agent.domain.emails import format_period
from finance_ops_agent.domain.investigation import retries_on_try_again
from finance_ops_agent.domain.invoice_numbers import problem_with_chosen_number
from finance_ops_agent.domain.items import Item, OutgoingRecord
from finance_ops_agent.domain.messages import StoredMessage
from finance_ops_agent.domain.money import Hours
from finance_ops_agent.domain.reading import (
    ReplyAnswer,
    ReplyAnswerKind,
    ReplyContext,
    ReplyReading,
)
from finance_ops_agent.domain.review import ReviewCode
from finance_ops_agent.domain.statuses import ItemStatus
from finance_ops_agent.ports.inbox import NEEDS_REVIEW_FOLDER

_ANSWERABLE = (
    "approval_request",
    "review_email",
    "ask_again_email",
    "reply_email",
    "setup_request",
)

APPROVAL_CODE = "APPROVAL"

# The requests that change what happens to an invoice. Each must rest on words
# that are really in Kevin's reply, not only on the model's say-so.
_MUST_BE_QUOTED = frozenset(
    {
        ReplyAnswerKind.APPROVE,
        ReplyAnswerKind.CANCEL,
        ReplyAnswerKind.INVOICE_NUMBER,
        ReplyAnswerKind.TRY_AGAIN,
        ReplyAnswerKind.SHOW_ME_FIRST,
    }
)


def _stripped_subject(subject: str) -> str:
    """The subject without its Re:/Fwd: prefixes, and with runs of whitespace
    squeezed to one space: a long subject is folded across lines by the sending
    mail client, and comes back with the odd extra space in it."""
    without_prefix = re.sub(r"^\s*((re|fwd?)\s*:\s*)+", "", subject.strip(), flags=re.IGNORECASE)
    return " ".join(without_prefix.split())


def _matched_record(deps: RunDeps, message: StoredMessage) -> OutgoingRecord | None:
    records = [record for record in deps.store.outgoing_records() if record.kind in _ANSWERABLE]
    answered = {message.in_reply_to, *message.references} - {""}
    if answered:
        by_id = [record for record in records if record.message_id in answered]
        if by_id:
            return by_id[-1]
    subject = _stripped_subject(message.subject).casefold()
    matches = [
        record
        for record in records
        if _stripped_subject(str(record.payload.get("subject", ""))).casefold() == subject
    ]
    return matches[-1] if matches else None


def _first_word(text: str) -> str:
    match = re.search(r"[a-zA-Z]+", text)
    return match.group(0).casefold() if match else ""


def _squeezed(text: str) -> str:
    return " ".join(text.split()).casefold()


def _is_quoted(answer: ReplyAnswer, body: str) -> bool:
    """The words the model relied on are really in the reply, as whole words:
    a quote of "A" must be Kevin's "A", not the a in "thanks"."""
    quote = _squeezed(answer.quote or "")
    if not quote:
        return False
    pattern = rf"(?<!\w){re.escape(quote)}(?!\w)"
    return re.search(pattern, _squeezed(body)) is not None


def _what_it_answers(record: OutgoingRecord) -> str:
    """The kind of email this reply is ultimately about.

    A reply to the agent's own follow-up ("Sorry to ask again", "Here is what I
    did") is about whatever that follow-up was about, so it goes the same way.
    """
    about = record.payload.get("answers_kind")
    return about if isinstance(about, str) and about else record.kind


@dataclass
class _Outcome:
    """What one reply led to, for the email back to Kevin."""

    done: list[str] = field(default_factory=list)
    not_done: list[str] = field(default_factory=list)


def _context(
    deps: RunDeps, item: Item | None, asked: str, offered: list[str] | None = None
) -> ReplyContext:
    if item is None:
        return ReplyContext(asked=asked, offered=offered or [])
    return ReplyContext(
        asked=asked,
        consultant=item.consultant,
        client=item.client,
        period=format_period(item.period),
        invoice_number=outgoing.planned_invoice_number(deps, item),
        offered=offered or [],
    )


def _write_back(
    deps: RunDeps,
    message: StoredMessage,
    record: OutgoingRecord,
    item_id: int | None,
    reading: ReplyReading | None,
    outcome: _Outcome,
    fallback_question: str,
) -> None:
    """Tell Kevin, in the same thread, what his reply led to."""
    lines: list[str] = []
    if outcome.not_done and not outcome.done:
        lines.append("Sorry to ask again — I couldn't do everything your reply asked.")
    elif outcome.not_done:
        lines.append("Thanks. I did part of what you asked, and need one more thing.")
    else:
        lines.append("Thanks.")
    if reading is not None and reading.understood:
        lines += ["", f"What I understood: {reading.understood}"]
    if outcome.done:
        lines += ["", "What happens now:", *[f"- {line}" for line in outcome.done]]
    if outcome.not_done:
        lines += ["", "What I couldn't do:", *[f"- {line}" for line in outcome.not_done]]
        question = (reading.still_unclear if reading is not None else "") or fallback_question
        lines += ["", question]
    about = _stripped_subject(str(record.payload.get("subject", "")))
    email = emails.OutgoingEmail(
        to=(deps.settings.admin_email,),
        subject=f"Re: {about}",
        in_reply_to=message.message_id,
        body="\n".join(lines),
    )
    outgoing.enqueue_email(
        deps,
        "reply_email",
        f"reply:{message.message_id}",
        item_id,
        email,
        {"answers_kind": _what_it_answers(record)},
    )


def handle_kevin_reply(deps: RunDeps, message: StoredMessage, report: RunReport) -> None:
    body = message.body_text or message.subject
    record = _matched_record(deps, message)
    if record is None:
        _tell_kevin_it_matched_nothing(deps, message, report)
        return
    if _what_it_answers(record) == "approval_request":
        _handle_approval_reply(deps, message, record, body, report)
    else:
        _handle_review_reply(deps, message, record, body, report)


# How much of Kevin's email is quoted back to him when it matched nothing.
_QUOTED_BACK = 300


def _tell_kevin_it_matched_nothing(
    deps: RunDeps, message: StoredMessage, report: RunReport
) -> None:
    """Kevin wrote and nothing the agent sent is what it answers (decision 63).

    Nothing is done with it: guessing what an email answers is how an
    instruction lands on the wrong invoice. But Kevin hears so, in the same
    thread, with the start of what he wrote, rather than finding out days later
    that nothing happened. Once only: an answer to that note -- or an
    out-of-office reply to it -- matches nothing either, and is not answered
    again."""
    report.note(f'a reply from Kevin I couldn\'t match: "{message.subject}"')
    ours = {
        record.message_id
        for record in deps.store.outgoing_records()
        if record.kind == "unmatched_reply_email" and record.message_id
    }
    if ours & ({message.in_reply_to, *message.references} - {""}):
        return  # an answer to the note itself: it has said all it can
    text = " ".join((message.body_text or "").split())
    quoted = text[:_QUOTED_BACK] + ("..." if len(text) > _QUOTED_BACK else "")
    lines = [
        "I couldn't tell which of my emails this answers, so I haven't done anything with it.",
        "",
        f'Your email: "{message.subject or "(no subject)"}", {message.received_at:%Y-%m-%d %H:%M}',
    ]
    if quoted:
        lines.append(f'It starts: "{quoted}"')
    lines += [
        "",
        "What you can do:",
        "- Reply to the email of mine it is about, keeping its subject, and put your answer there.",
    ]
    if message.attachments:
        lines.append(
            "- To have me read a timesheet, it has to come from the consultant's own"
            " address, or from an address on the forwarders list."
        )
    lines.append("- If it wasn't meant for me, there is nothing to do.")
    note = emails.OutgoingEmail(
        to=(deps.settings.admin_email,),
        subject=f"Re: {message.subject}" if message.subject else "Your email to me",
        in_reply_to=message.message_id,
        body="\n".join(lines),
    )
    outgoing.enqueue_email(
        deps, "unmatched_reply_email", f"unmatched:{message.message_id}", None, note
    )


# --- approvals ---


def _approve(deps: RunDeps, item: Item, report: RunReport) -> None:
    outgoing.approve_item(deps, item, report)
    report.note(f"Kevin approved: {item.consultant} at {item.client}")


def _cancel(deps: RunDeps, item: Item, report: RunReport) -> None:
    # The invoice exists by now: it was made so Kevin could approve the real
    # thing (decision 33), so cancelling has to void it.
    outgoing.cancel_invoices(deps, item, report)
    deps.store.change_status(item.id, ItemStatus.CANCELLED, {"why": "Kevin cancelled"})
    report.note(f"Kevin cancelled: {item.consultant} at {item.client}")


_APPROVAL_FALLBACK = (
    'Reply "approve" to send it to the client as it is, "cancel" to stop it'
    ' (the invoice is voided), or "wrong client" if the timesheet is for another'
    ' client. Plain words work too, like "looks good, send it".'
)


def _handle_approval_reply(
    deps: RunDeps,
    message: StoredMessage,
    record: OutgoingRecord,
    body: str,
    report: RunReport,
) -> None:
    assert record.item_id is not None
    item = deps.store.get_item(record.item_id)
    word = _first_word(body)
    if re.findall(r"[a-zA-Z]+", body.casefold())[:2] == ["wrong", "client"]:
        # Read by code, as "approve" and "cancel" are (decision 57).
        _handle_wrong_client(deps, message, record, item, report)
        return
    if word in ("approve", "cancel"):
        # The plain word is taken as it stands, without the model.
        if item.status is ItemStatus.WAITING_FOR_APPROVAL:
            (_approve if word == "approve" else _cancel)(deps, item, report)
        return
    if item.status is not ItemStatus.WAITING_FOR_APPROVAL:
        return  # it moved on (approved or cancelled already); nothing to decide
    number = outgoing.planned_invoice_number(deps, item)
    question = (
        f"Kevin was shown invoice {number or '(numbered)'} for {item.consultant} at"
        f" {item.client} ({format_period(item.period)}) and asked to approve sending it"
        " to the client as it is, or cancel it."
    )
    reading = deps.reader.read_reply(
        body, [(APPROVAL_CODE, question)], _context(deps, item, "approval")
    )
    approve = [a for a in reading.answers if a.kind is ReplyAnswerKind.APPROVE]
    cancel = [a for a in reading.answers if a.kind is ReplyAnswerKind.CANCEL]
    # Things that change nothing about the invoice he was shown.
    harmless = (
        ReplyAnswerKind.APPROVE,
        ReplyAnswerKind.SHOW_ME_FIRST,
        ReplyAnswerKind.APPROVAL_NOTE,
    )
    changes = [a for a in reading.answers if a.kind not in (*harmless, ReplyAnswerKind.CANCEL)]
    outcome = _Outcome()
    if approve and not cancel and not changes and all(_is_quoted(a, body) for a in approve):
        _approve(deps, item, report)
        outcome.done.append(
            f"Sent invoice {number} to {item.client}, with you on CC"
            f' (you said "{approve[0].quote}").'
        )
    elif cancel and not approve and all(_is_quoted(a, body) for a in cancel):
        _cancel(deps, item, report)
        outcome.done.append(
            f"Cancelled it and voided invoice {number} in QuickBooks; nothing went to"
            f' the client (you said "{cancel[0].quote}").'
        )
    else:
        if approve and cancel:
            outcome.not_done.append("I read both approve and cancel in your reply.")
        for change in changes:
            if change.kind is ReplyAnswerKind.UNCLEAR:
                continue
            outcome.not_done.append(
                f"The invoice already exists in QuickBooks as {number}, so I can't"
                f' change it from here ("{change.quote or change.value}"). I can send it'
                " as it is, or cancel it and void it."
            )
        if not outcome.not_done:
            outcome.not_done.append("I couldn't tell whether you want it sent.")
        report.note(f"asked Kevin again about approving {item.consultant} at {item.client}")
    _write_back(deps, message, record, item.id, reading, outcome, _APPROVAL_FALLBACK)


# --- review answers ---

_REVIEW_FALLBACK = (
    "Could you say it another way? Replies like these work: "
    '"this is for Acme", "use 152 hours", "approved by Jane Doe on 9/3",'
    ' "use the new one", "use invoice number 083126MT-MK-revised",'
    ' "I deleted the old one, try again", "ignore".'
)


def _check_answer(
    deps: RunDeps, item: Item | None, answer: ReplyAnswer, body: str
) -> tuple[bool, str]:
    """Whether code accepts one request, and the line Kevin will read about it."""
    kind = answer.kind
    if kind in _MUST_BE_QUOTED and not _is_quoted(answer, body):
        return False, "I wasn't sure enough what you meant, so I did nothing with it."
    if kind is ReplyAnswerKind.INVOICE_NUMBER:
        number = (answer.value or "").strip()
        problem = problem_with_chosen_number(number)
        if problem is None and deps.store.invoice_number_in_use(number):
            problem = f"one of my invoices already has {number}"
        if problem is None and item is not None:
            live = [r for r in deps.store.invoices_for_item(item.id) if r.status != "cancelled"]
            if live:
                problem = f"the invoice already exists in QuickBooks as {live[-1].number}"
        if problem is not None:
            return False, f"I can't use invoice number {number or '(none given)'}: {problem}."
        return True, f"I'll make the invoice as {number}."
    if kind is ReplyAnswerKind.HOURS:
        try:
            hours = Hours.parse(str(answer.value or ""))
        except ValueError:
            return False, f'I can\'t read "{answer.value}" as a number of hours.'
        return True, f"I'll use {hours} hours."
    if kind is ReplyAnswerKind.TRY_AGAIN:
        # Only reached where an invoice or a send failed: anywhere else it is
        # acknowledged and the question left open, before this is asked.
        return True, "I'm trying again now."
    if kind is ReplyAnswerKind.SHOW_ME_FIRST:
        if deps.settings.mode is Mode.DRY_RUN:
            return True, "Nothing goes to a client in dry run; you'll get the preview as usual."
        return True, "You'll get the invoice to approve before anything goes to the client."
    if kind is ReplyAnswerKind.USE_NEW_ONE:
        return True, "I'm using the corrected timesheet."
    if kind in (ReplyAnswerKind.APPROVE, ReplyAnswerKind.CANCEL):
        return False, (
            "There's no invoice waiting for your approval yet. You'll get it to"
            " approve once I've made it."
        )
    label = kind.value.replace("_", " ")
    return True, f"Noted the {label}: {answer.value}."


def _handle_wrong_client(
    deps: RunDeps,
    message: StoredMessage,
    record: OutgoingRecord,
    item: Item,
    report: RunReport,
) -> None:
    """Right consultant, wrong client (decision 57).

    The draft is voided, as "cancel" voids it. The item is not cancelled: its
    engagement's period still needs its own timesheet, so it goes back to
    waiting for one, and the timesheet that was wrongly put on it is detached
    so it can be read again. That email is then set aside with the setup form,
    never to be put on this client again, and is handled once Kevin has said
    -- or set up -- the client it is for."""
    if item.status is not ItemStatus.WAITING_FOR_APPROVAL:
        ask = emails.OutgoingEmail(
            to=(deps.settings.admin_email,),
            subject=f"Re: {record.payload.get('subject', '')}",
            in_reply_to=message.message_id,
            body=(
                f"This invoice is no longer waiting for your approval (it is"
                f" {item.status.value.replace('_', ' ')}), so I haven't changed anything."
                " If it has already gone to the client, it needs correcting as a"
                " corrected timesheet would."
            ),
        )
        outgoing.enqueue_email(
            deps, "ask_again_email", f"wrongclient:{message.message_id}", item.id, ask
        )
        return
    timesheet = outgoing.active_timesheet_attachment(deps, item)
    originals = deps.store.message_ids_for_item(item.id)
    wrong = item.client
    outgoing.cancel_invoices(deps, item, report)
    item = deps.store.put_back_to_waiting(item.id, f"Kevin said the timesheet is not for {wrong}")
    report.note(f"Kevin said wrong client: {item.consultant}'s timesheet is not for {wrong}")
    for message_id in originals:
        original = deps.store.get_message(message_id)
        if original is None:
            continue
        review = (
            f'You said {item.consultant}\'s timesheet ("{original.subject}") is not for'
            f" {wrong}. I voided that invoice; {wrong} is waiting for its own timesheet for"
            f" the same dates. Which client is this one for?"
        )
        if deps.store.open_review(None, ReviewCode.ENGAGEMENT_UNCLEAR.value, review):
            report.reviews_opened += 1
        set_aside.set_aside(
            deps,
            original,
            ReviewCode.ENGAGEMENT_UNCLEAR,
            review,
            email_kevin=True,
            timesheet=timesheet,
            prefill_extra={
                setup.CONSULTANT: item.consultant,
                setup.START: item.period.start.isoformat(),
            },
            not_client=wrong,
        )
        outages.file_in(deps, message_id, NEEDS_REVIEW_FOLDER)


def _handle_review_reply(
    deps: RunDeps,
    message: StoredMessage,
    record: OutgoingRecord,
    body: str,
    report: RunReport,
) -> None:
    # Answers about one set-aside email, or about QuickBooks being unreachable,
    # go to their own handlers: put to the reader alongside every other open
    # question without an item, an "ignore" would close them all (decision 55).
    if record.payload.get("set_aside"):
        set_aside.handle_reply(deps, message, record, body, report)
        return
    if record.payload.get("engagement_refresh"):
        engagement_copy.handle_reply(deps, message, record, body, report)
        return
    if record.payload.get("outage"):
        # Read by code, not Claude: Claude may be the thing that is down.
        if _first_word(body) == "ignore":
            outages.close_review(deps, str(record.payload["outage"]))
            report.note("Kevin closed the question about an outage")
        return
    item = None if record.item_id is None else deps.store.get_item(record.item_id)
    if record.payload.get("uncertain_key"):
        outgoing.answer_send_uncertain(deps, record, body, report)
        return
    open_reviews = [
        review for review in deps.store.open_reviews() if review.item_id == record.item_id
    ]
    if not open_reviews:
        return  # nothing left to answer; the item moved on
    questions = [(review.code, review.message) for review in open_reviews]
    offered = offered_replies(deps, open_reviews)
    picked = picked_option(body, offered)
    if picked is not None:
        # "A" stands for the words that option offered; read and check those,
        # exactly as if he had written them (decision 61).
        report.note(f'Kevin picked an option: "{picked}"')
        body = picked
    reading = deps.reader.read_reply(body, questions, _context(deps, item, "review", offered))
    outcome = _Outcome()

    if any(answer.kind is ReplyAnswerKind.IGNORE for answer in reading.answers):
        for review in open_reviews:
            deps.store.answer_review(review.id, {"kind": "ignore"}, "ignored")
        if item is not None and item.status in (ItemStatus.RECEIVED, ItemStatus.NEEDS_REVIEW):
            deps.store.change_status(item.id, ItemStatus.IGNORED, {"why": "Kevin said ignore"})
        report.note("Kevin said ignore")
        return

    accepted: dict[int, list[ReplyAnswer]] = {}
    acknowledged = False
    for answer in reading.answers:
        if answer.kind is ReplyAnswerKind.UNCLEAR:
            outcome.not_done.append(
                "I couldn't tell what your reply means for: "
                + next(
                    (r.message for r in open_reviews if r.code == answer.review_code),
                    "what I asked",
                )
            )
            continue
        target = next(
            (review for review in open_reviews if review.code == answer.review_code),
            open_reviews[0],
        )
        if (
            answer.kind is ReplyAnswerKind.TRY_AGAIN
            and not retries_on_try_again(target.code, target.message)
            and _is_quoted(answer, body)
        ):
            # Nothing failed here that could be attempted again, and nothing to
            # record: whatever waits on QuickBooks or the engagement list is
            # looked at again on every run anyway, and closing the question
            # would let an item go ahead on figures nobody confirmed (decision
            # 55). So it stays open, and Kevin is told so (decision 61).
            report.note("Kevin asked me to try again; I do on every run")
            outcome.done.append(
                "I look again on every run, so this question stays open until it is"
                " settled. Nothing else has changed."
            )
            acknowledged = True
            continue
        ok, line = _check_answer(deps, item, answer, body)
        if not ok:
            outcome.not_done.append(line)
            continue
        outcome.done.append(line)
        accepted.setdefault(target.id, []).append(answer)
    if not reading.answers:
        outcome.not_done.append("I couldn't find an answer in your reply.")

    for review_id, answers in accepted.items():
        first = answers[0]
        if item is not None and any(a.kind is ReplyAnswerKind.USE_NEW_ONE for a in answers):
            deps.store.accept_correction(item.id)
            report.note(f"using the corrected timesheet for {item.consultant}")
        deps.store.answer_review(
            review_id,
            {
                "kind": first.kind.value,
                "value": first.value,
                "quote": first.quote,
                "actions": [
                    {"kind": a.kind.value, "value": a.value, "quote": a.quote} for a in answers
                ],
            },
            "answered",
        )
    if accepted:
        report.note(f"applied Kevin's answer: {'; '.join(outcome.done)}")

    if (
        outcome.not_done
        or acknowledged
        or any(
            a.kind not in (ReplyAnswerKind.HOURS, ReplyAnswerKind.USE_NEW_ONE)
            for answers in accepted.values()
            for a in answers
        )
    ):
        # A plain "use 152 hours" shows up as the next email about the item;
        # anything else gets said back to him, so he knows what was done.
        _write_back(deps, message, record, record.item_id, reading, outcome, _REVIEW_FALLBACK)
    if outcome.not_done or item is None:
        return
    item = deps.store.get_item(item.id)
    still_open = [review for review in deps.store.open_reviews() if review.item_id == item.id]
    if still_open or item.status not in (ItemStatus.NEEDS_REVIEW, ItemStatus.RECEIVED):
        return
    if item.status is ItemStatus.NEEDS_REVIEW:
        item = deps.store.change_status(item.id, ItemStatus.RECEIVED, {"why": "Kevin answered"})
    complete_if_covered(deps, item, report)
