"""Kevin's replies: approve/cancel by code, review answers read by Claude.

Only replies from Kevin's address count (the run routes by sender), and a
reply is matched to what it answers by the subject of the email it replies to.
Approvals are checked by code - the first word must be approve or cancel; only
review answers go through the reader, and code applies them.
"""

import re

from finance_ops_agent.application import outgoing
from finance_ops_agent.application.completion import complete_if_covered
from finance_ops_agent.application.context import RunDeps, RunReport
from finance_ops_agent.domain import emails
from finance_ops_agent.domain.items import Item, OutgoingRecord, ReviewRecord
from finance_ops_agent.domain.messages import StoredMessage
from finance_ops_agent.domain.reading import ReplyAnswer, ReplyAnswerKind
from finance_ops_agent.domain.statuses import ItemStatus

_ANSWERABLE = ("approval_request", "review_email", "ask_again_email")


def _stripped_subject(subject: str) -> str:
    return re.sub(r"^\s*((re|fwd?)\s*:\s*)+", "", subject.strip(), flags=re.IGNORECASE)


def _matched_record(deps: RunDeps, message: StoredMessage) -> OutgoingRecord | None:
    subject = _stripped_subject(message.subject).casefold()
    matches = [
        record
        for record in deps.store.outgoing_records()
        if record.kind in _ANSWERABLE
        and str(record.payload.get("subject", "")).casefold() == subject
    ]
    return matches[-1] if matches else None


def _first_word(text: str) -> str:
    match = re.search(r"[a-zA-Z]+", text)
    return match.group(0).casefold() if match else ""


def handle_kevin_reply(deps: RunDeps, message: StoredMessage, report: RunReport) -> None:
    body = message.body_text or message.subject
    record = _matched_record(deps, message)
    if record is None:
        report.note(f'a reply from Kevin I couldn\'t match: "{message.subject}"')
        return
    if record.kind == "approval_request":
        _handle_approval_reply(deps, message, record, body, report)
    else:
        _handle_review_reply(deps, message, record, body, report)


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
    if word == "approve":
        if item.status is ItemStatus.WAITING_FOR_APPROVAL:
            outgoing.approve_item(deps, item, report)
            report.note(f"Kevin approved: {item.consultant} at {item.client}")
    elif word == "cancel":
        if item.status is ItemStatus.WAITING_FOR_APPROVAL:
            deps.store.change_status(item.id, ItemStatus.CANCELLED, {"why": "Kevin cancelled"})
            report.note(f"Kevin cancelled: {item.consultant} at {item.client}")
    else:
        ask = emails.OutgoingEmail(
            to=(deps.settings.admin_email,),
            subject=f"Re: {record.payload.get('subject', '')}",
            body=(
                'Sorry - I only understand replies that start with "approve" or'
                ' "cancel" on this one. Nothing has been sent.'
            ),
        )
        outgoing.enqueue_email(
            deps, "ask_again_email", f"askword:{message.provider_id}", item.id, ask
        )


def _apply_answer(
    deps: RunDeps,
    item: Item | None,
    open_reviews: list[ReviewRecord],
    answer: ReplyAnswer,
    report: RunReport,
) -> bool:
    """Apply one typed answer; returns False when the agent must ask again."""
    target = next(
        (review for review in open_reviews if review.code == answer.review_code),
        open_reviews[0] if open_reviews else None,
    )
    if answer.kind is ReplyAnswerKind.UNCLEAR or target is None:
        return False
    if answer.kind is ReplyAnswerKind.IGNORE:
        for review in open_reviews:
            deps.store.answer_review(review.id, {"kind": "ignore"}, "ignored")
        if item is not None and item.status in (
            ItemStatus.RECEIVED,
            ItemStatus.NEEDS_REVIEW,
        ):
            deps.store.change_status(item.id, ItemStatus.IGNORED, {"why": "Kevin said ignore"})
        report.note("Kevin said ignore")
        return True
    if answer.kind is ReplyAnswerKind.USE_NEW_ONE and item is not None:
        deps.store.accept_correction(item.id)
        report.note(f"using the corrected timesheet for {item.consultant}")
    deps.store.answer_review(
        target.id,
        {"kind": answer.kind.value, "value": answer.value, "quote": answer.quote},
        "answered",
    )
    return True


def _handle_review_reply(
    deps: RunDeps,
    message: StoredMessage,
    record: OutgoingRecord,
    body: str,
    report: RunReport,
) -> None:
    item = None if record.item_id is None else deps.store.get_item(record.item_id)
    open_reviews = [
        review for review in deps.store.open_reviews() if review.item_id == record.item_id
    ]
    if not open_reviews:
        return  # nothing left to answer; the item moved on
    questions = [(review.code, review.message) for review in open_reviews]
    reply = deps.reader.read_reply(body, questions)
    unclear = False
    for answer in reply.answers:
        if not _apply_answer(deps, item, open_reviews, answer, report):
            unclear = True
    if unclear:
        about = str(record.payload.get("subject", "your review"))
        ask = emails.OutgoingEmail(
            to=(deps.settings.admin_email,),
            subject=f"Re: {about}",
            body=(
                "Sorry to ask again - I couldn't tell what your reply means for"
                " everything I asked. Could you answer in one of these shapes?\n"
                '"this is for Acme", "use 152 hours",'
                ' "approved by Jane Doe on 9/3", "use the new one", "ignore".'
            ),
        )
        outgoing.enqueue_email(
            deps, "ask_again_email", f"askagain:{message.provider_id}", record.item_id, ask
        )
        return
    if item is None:
        return
    item = deps.store.get_item(item.id)
    still_open = [review for review in deps.store.open_reviews() if review.item_id == item.id]
    if still_open or item.status not in (ItemStatus.NEEDS_REVIEW, ItemStatus.RECEIVED):
        return
    if item.status is ItemStatus.NEEDS_REVIEW:
        item = deps.store.change_status(item.id, ItemStatus.RECEIVED, {"why": "Kevin answered"})
    complete_if_covered(deps, item, report)
