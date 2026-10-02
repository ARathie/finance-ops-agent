"""The engagement list a run works from, and the copy of it kept between runs.

In QuickBooks mode (decision 53) the engagement list is built from QuickBooks:
every product, every customer, every vendor. Who sends timesheets, which client
an engagement bills and how often hardly ever change, so the agent keeps what
it built in its own store and works from that copy (decision 55). The copy is
taken again:

- on the day's first run, so anything changed in QuickBooks is picked up the
  same day;
- when something does not match it -- an email from an address it does not
  know, or a timesheet naming someone it cannot place -- because that is
  exactly what a new consultant or a changed address looks like;
- when Kevin replies "try again".

What the copy is never used for is money on its own: the rates, the payee and
the client's billing details are asked of QuickBooks again for the engagement
in hand when its timesheet is read (decision 43).

When QuickBooks cannot be asked, the run carries on from the last copy (or the
spreadsheet, if there is no copy yet), and Kevin is told once, in an email that
says what he can do. The review closes by itself once QuickBooks answers.
"""

import dataclasses
import hashlib
import json
import types
from datetime import date
from enum import Enum
from typing import Any, Union, get_args, get_origin, get_type_hints

from finance_ops_agent import logs
from finance_ops_agent.application import outgoing as outgoing_steps
from finance_ops_agent.application.context import RunDeps, RunReport
from finance_ops_agent.application.from_quickbooks import workbook_from_accounting
from finance_ops_agent.domain import emails
from finance_ops_agent.domain.engagements import EngagementWorkbook, parse_workbook
from finance_ops_agent.domain.items import OutgoingRecord
from finance_ops_agent.domain.messages import StoredMessage
from finance_ops_agent.domain.money import Money
from finance_ops_agent.domain.reading import ReplyAnswerKind
from finance_ops_agent.domain.review import ReviewCode
from finance_ops_agent.ports.accounting import AccountingFailed, AccountingNeedsReconnect

COPY_KEY = "engagement_copy"
COPY_TAKEN_KEY = "engagement_copy_taken"
REFRESH_REQUESTED_KEY = "engagement_copy_refresh_requested"
# Set when Kevin has been told QuickBooks cannot be asked, cleared when it
# answers again: told once per outage, and an "ignore" holds until it is over.
REFRESH_FLAGGED_KEY = "engagement_copy_refresh_flagged"
REFRESH_FAILED = "I couldn't refresh my copy of the engagements from QuickBooks"


# --- the copy, as it is kept ---


def _encode(value: object) -> object:
    if isinstance(value, Money):
        return value.cents
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _encode(getattr(value, field.name)) for field in dataclasses.fields(value)
        }
    if isinstance(value, (list, tuple)):
        return [_encode(part) for part in value]
    return value


def _decode(kind: Any, value: Any) -> Any:
    origin = get_origin(kind)
    if origin in (Union, types.UnionType):
        if value is None:
            return None
        (inner,) = [part for part in get_args(kind) if part is not type(None)]
        return _decode(inner, value)
    if origin is list:
        (inner,) = get_args(kind)
        return [_decode(inner, part) for part in value]
    if origin is tuple:
        inner = get_args(kind)[0]
        return tuple(_decode(inner, part) for part in value)
    if kind is Money:
        return Money(int(value))
    if kind is date:
        return date.fromisoformat(value)
    if isinstance(kind, type) and issubclass(kind, Enum):
        return kind(value)
    if dataclasses.is_dataclass(kind) and isinstance(kind, type):
        hints = get_type_hints(kind)
        return kind(**{name: _decode(hints[name], value[name]) for name in hints if name in value})
    return value


def workbook_to_json(workbook: EngagementWorkbook) -> str:
    return json.dumps(_encode(workbook), sort_keys=True)


def workbook_from_json(text: str) -> EngagementWorkbook:
    decoded: EngagementWorkbook = _decode(EngagementWorkbook, json.loads(text))
    return decoded


# --- the engagement list for one run ---


class Engagements:
    """The engagement list one run works from.

    `complete` says whether it is what the setting asks for, as of today: the
    spreadsheet, or a copy of QuickBooks taken today. The daily look for ended
    periods is only counted as done on a complete list, so an outage cannot
    hide a period until tomorrow.
    """

    def __init__(self, deps: RunDeps, report: RunReport) -> None:
        self._deps = deps
        self._report = report
        self._asked_this_run = False
        self.refreshed = False  # a fresh copy was taken during this run
        self.complete = True
        self.workbook = self._load()

    @property
    def from_quickbooks(self) -> bool:
        return self._deps.settings.engagements_from == "quickbooks"

    def _load(self) -> EngagementWorkbook:
        deps = self._deps
        if not self.from_quickbooks:
            return parse_workbook(deps.engagement_list.load())
        copy = _stored_copy(deps)
        taken = deps.store.get_state(COPY_TAKEN_KEY)
        requested = bool(deps.store.get_state(REFRESH_REQUESTED_KEY))
        if copy is not None and taken == deps.clock.today().isoformat() and not requested:
            return copy
        fresh = self._ask_quickbooks("the day's first look" if not requested else "Kevin asked")
        if fresh is not None:
            return fresh
        self.complete = False
        if copy is not None:
            return copy
        return parse_workbook(deps.engagement_list.load())

    def refresh_on_miss(self, why: str) -> bool:
        """Take a fresh copy because something did not match the one in hand.

        At most once a run: a second miss in the same run would only ask
        QuickBooks the same question again. Returns whether the list changed
        to a fresh copy, so the caller knows whether looking again can help.
        """
        if not self.from_quickbooks or self._asked_this_run:
            return False
        fresh = self._ask_quickbooks(why)
        if fresh is None:
            return False
        self.workbook = fresh
        return True

    def refresh_now(self, why: str) -> bool:
        """Take a fresh copy even if one was taken this run: something the
        agent just set up in QuickBooks itself must be in it (decision 56)."""
        if not self.from_quickbooks:
            return False
        fresh = self._ask_quickbooks(why)
        if fresh is None:
            return False
        self.workbook = fresh
        return True

    def _ask_quickbooks(self, why: str) -> EngagementWorkbook | None:
        deps = self._deps
        self._asked_this_run = True
        logs.log("taking a fresh copy of the engagements from quickbooks", why=why)
        try:
            fresh = workbook_from_accounting(deps.accounting)
        except AccountingFailed as error:
            logs.log("could not build the engagements from quickbooks", said=str(error)[:200])
            if isinstance(error, AccountingNeedsReconnect):
                self._report.quickbooks_unavailable = True
            _flag_refresh_failed(deps, self._report, error)
            self._report.note(
                "QuickBooks could not be asked for the engagements, so this run used"
                + (
                    " my last copy of them"
                    if deps.store.get_state(COPY_KEY)
                    else " the engagement list"
                )
            )
            return None
        deps.store.set_state(COPY_KEY, workbook_to_json(fresh))
        deps.store.set_state(COPY_TAKEN_KEY, deps.clock.today().isoformat())
        deps.store.set_state(REFRESH_REQUESTED_KEY, "")
        _close_refresh_failed(deps, self._report)
        self.refreshed = True
        self.complete = True
        return fresh


def stored_copy(deps: RunDeps) -> EngagementWorkbook | None:
    """The last copy taken, for checking an answer of Kevin's against it
    between runs' looks."""
    return _stored_copy(deps)


def _stored_copy(deps: RunDeps) -> EngagementWorkbook | None:
    text = deps.store.get_state(COPY_KEY)
    if not text:
        return None
    try:
        return workbook_from_json(text)
    except (KeyError, TypeError, ValueError) as error:
        # A copy written by an older version that this one cannot read is
        # simply not a copy: the next look takes a new one.
        logs.log("the stored copy of the engagements could not be read", said=str(error)[:200])
        return None


# --- telling Kevin, and hearing back ---


def _refresh_reviews(deps: RunDeps) -> list[int]:
    return [
        review.id
        for review in deps.store.open_reviews()
        if review.item_id is None and review.message.startswith(REFRESH_FAILED)
    ]


def _flag_refresh_failed(deps: RunDeps, report: RunReport, error: AccountingFailed) -> None:
    """One review and one email while QuickBooks stays unreachable, not one a run."""
    if _refresh_reviews(deps) or deps.store.get_state(REFRESH_FLAGGED_KEY):
        return
    copy_taken = deps.store.get_state(COPY_TAKEN_KEY)
    working_from = (
        f"the copy I took on {copy_taken}"
        if copy_taken and deps.store.get_state(COPY_KEY)
        else "the engagement list spreadsheet"
    )
    reconnect = isinstance(error, AccountingNeedsReconnect)
    message = (
        f"{REFRESH_FAILED}, so I am working from {working_from}. A consultant or"
        " client added or changed in QuickBooks since then will not be recognised"
        f" until I can. QuickBooks said: {str(error)[:200]}"
    )
    code = ReviewCode.QUICKBOOKS_RECONNECT if reconnect else ReviewCode.QUICKBOOKS_FAILED
    if not deps.store.open_review(None, code.value, message):
        return
    deps.store.set_state(REFRESH_FLAGGED_KEY, "yes")
    report.reviews_opened += 1
    report.note(f"needs Kevin's review ({code.value}): {message}")
    what_you_can_do = [
        "Nothing, if QuickBooks is only briefly down: I try again on every run and"
        " close this by myself once it answers.",
        *(
            ["Run `fops qbo-connect` to reconnect QuickBooks."]
            if reconnect
            else ["Check that QuickBooks is reachable and the connection still works."]
        ),
        'Reply "try again" once it is fixed, and I will look again on my next run.',
        'Reply "ignore" to close this; I still keep trying by myself.',
    ]
    email = emails.needs_review(
        deps.settings.admin_email,
        "the engagements in QuickBooks",
        [message],
        what_you_can_do=what_you_can_do,
    )
    digest = hashlib.sha256(message.encode()).hexdigest()[:16]
    outgoing_steps.enqueue_email(
        deps,
        "review_email",
        f"engagement-refresh:{digest}",
        None,
        email,
        extra={"engagement_refresh": True},
    )


def _close_refresh_failed(deps: RunDeps, report: RunReport) -> None:
    deps.store.set_state(REFRESH_FLAGGED_KEY, "")
    for review_id in _refresh_reviews(deps):
        deps.store.answer_review(
            review_id, {"kind": "resolved", "why": "QuickBooks answered again"}, "answered"
        )
        report.note("QuickBooks answered again; my copy of the engagements is up to date")


def request_refresh(deps: RunDeps) -> None:
    """The next look takes a fresh copy whatever the date: Kevin said "try again"."""
    deps.store.set_state(REFRESH_REQUESTED_KEY, "yes")


def handle_reply(
    deps: RunDeps, reply: StoredMessage, record: OutgoingRecord, body: str, report: RunReport
) -> None:
    """Kevin answered the email saying QuickBooks could not be asked."""
    open_ids = _refresh_reviews(deps)
    questions = [
        (review.code, review.message)
        for review in deps.store.open_reviews()
        if review.id in open_ids
    ]
    if not questions:
        return  # QuickBooks answered in the meantime and the review closed itself
    kinds = {answer.kind for answer in deps.reader.read_reply(body, questions).answers}
    if ReplyAnswerKind.TRY_AGAIN in kinds:
        request_refresh(deps)
        report.note("Kevin asked me to try QuickBooks again")
    elif ReplyAnswerKind.IGNORE in kinds:
        for review_id in open_ids:
            deps.store.answer_review(review_id, {"kind": "ignore"}, "ignored")
        report.note("Kevin closed the QuickBooks question; I keep trying by myself")
    else:
        ask = emails.OutgoingEmail(
            to=(deps.settings.admin_email,),
            subject=f"Re: {record.payload.get('subject', '')}",
            in_reply_to=reply.message_id,
            body=(
                'Sorry - I couldn\'t tell what your reply means. Reply "try again" once'
                ' QuickBooks is fixed, or "ignore" to close this; either way I keep'
                " trying by myself on every run."
            ),
        )
        outgoing_steps.enqueue_email(
            deps,
            "ask_again_email",
            f"askagain:{reply.message_id}",
            None,
            ask,
            extra={"engagement_refresh": True},
        )
