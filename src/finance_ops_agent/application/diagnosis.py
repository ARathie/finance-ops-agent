"""Looking, never touching: what is stuck, and why (docs/decisions.md #60).

Every function here reads the agent's records, the accounting system and the
inbox, and returns findings in plain words: what is the case, and what a person
can do about it. None of them changes anything. They are typed against the
read-only views in `ports/looking.py`, so a write cannot be added here without
mypy refusing it, and a test runs them against stores that fail on any write.

They are written to be the first tools of an agent that diagnoses problems on
its own: each takes plain arguments and returns plain data. Until that exists,
`fops diagnose` runs them for a person.

The cases are the ones the first live cycle hit: an invoice in the agent's
records that QuickBooks does not have, an invoice number QuickBooks already
holds, an email in the inbox that the agent will never read, and items left over
from testing or from a start date that is too early.
"""

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from finance_ops_agent.application.context import RunDeps
from finance_ops_agent.domain.emails import dollars, format_period
from finance_ops_agent.domain.engagements import EngagementWorkbook
from finance_ops_agent.domain.invoice_numbers import is_voided_number
from finance_ops_agent.domain.items import Item
from finance_ops_agent.domain.money import Money
from finance_ops_agent.domain.statuses import ItemStatus
from finance_ops_agent.ports.accounting import AccountingFailed, InvoiceLookup
from finance_ops_agent.ports.looking import AccountingToLookAt, InboxToLookAt, StoreToLookAt

FINAL = (ItemStatus.CLIENT_PAID, ItemStatus.IGNORED, ItemStatus.CANCELLED)
# Waiting for this many periods of one engagement at once usually means the
# start date is earlier than the work: the agent is chasing months nobody owes.
MANY_WAITING = 3
# The number in the review email for a number QuickBooks already has.
_TAKEN_NUMBER = re.compile(r"already has an invoice numbered (\S+?),")


class FindingKind(StrEnum):
    """What diagnosis found. Not review codes: nothing here opens a review."""

    ACCOUNTING_UNREACHABLE = "accounting_unreachable"
    INVOICE_NOT_IN_ACCOUNTING = "invoice_not_in_accounting"
    INVOICE_RENUMBERED = "invoice_renumbered"
    INVOICE_FOR_ANOTHER_ITEM = "invoice_for_another_item"
    NUMBER_HELD_BY_FORGOTTEN_ITEM = "number_held_by_forgotten_item"
    NUMBER_HELD_BY_ANOTHER_ITEM = "number_held_by_another_item"
    NUMBER_HELD_BY_HAND = "number_held_by_hand"
    NUMBER_FREE = "number_free"
    EMAIL_ALREADY_HANDLED = "email_already_handled"
    EMAIL_WAITING_TO_BE_PROCESSED = "email_waiting_to_be_processed"
    EMAIL_BEFORE_START_DATE = "email_before_start_date"
    EMAIL_ALREADY_READ_PAST = "email_already_read_past"
    EMAIL_WILL_BE_READ = "email_will_be_read"
    ITEM_NOT_ON_ENGAGEMENT_LIST = "item_not_on_engagement_list"
    MANY_PERIODS_WAITING = "many_periods_waiting"


@dataclass(frozen=True)
class Finding:
    kind: FindingKind
    what: str  # what is the case, in plain words
    next_step: str  # what a person can do about it; empty when nothing is needed
    item_id: int | None = None
    needs_attention: bool = True  # False for "this is fine", said so nobody wonders
    # The one thing it is about, for code that acts on it: an invoice's id in
    # the accounting system, an invoice number, or an email's Message-ID.
    about: str = ""


@dataclass(frozen=True)
class Looking:
    """Everything diagnosis may read. Built by the caller; never written to."""

    store: StoreToLookAt
    accounting: AccountingToLookAt
    inbox: InboxToLookAt | None = None
    mailbox_position: str | None = None
    mail_start_date: date | None = None
    workbook: EngagementWorkbook | None = None


def looking_at(deps: RunDeps, workbook: EngagementWorkbook | None = None) -> Looking:
    """What a run may look at: its own store and accounting system. Not the
    inbox -- the run has just read it, and listing it again is a second
    connection for nothing."""
    return Looking(store=deps.store, accounting=deps.accounting, workbook=workbook)


def _describe(item: Item) -> str:
    return (
        f"item {item.id} ({item.consultant} at {item.client},"
        f" {format_period(item.period)}, {item.status.value})"
    )


def _money(cents: int) -> str:
    return dollars(Money(cents))


def _items_by_id(looking: Looking) -> dict[int, Item]:
    return {item.id: item for item in looking.store.list_items()}


def _unreachable(error: AccountingFailed) -> Finding:
    return Finding(
        FindingKind.ACCOUNTING_UNREACHABLE,
        f"I couldn't ask QuickBooks: {error}",
        "Run `fops doctor` to check the connection, then try again.",
    )


# --- the agent's invoices against the accounting system ---


def check_recorded_invoices(looking: Looking) -> list[Finding]:
    """Every live invoice in the agent's records, looked up in QuickBooks.

    An invoice QuickBooks does not have is what makes the daily paid check fail
    on every run, for every invoice, until it is dealt with.
    """
    if not looking.accounting.can_look_up_invoices:
        return []
    findings: list[Finding] = []
    for item in looking.store.list_items():
        for record in looking.store.invoices_for_item(item.id):
            if record.status == "cancelled":
                continue
            try:
                held = looking.accounting.invoice_lookup(record.external_id)
            except AccountingFailed as error:
                return [*findings, _unreachable(error)]
            if held is None:
                findings.append(
                    Finding(
                        FindingKind.INVOICE_NOT_IN_ACCOUNTING,
                        f"My records say invoice {record.number} (QuickBooks id"
                        f" {record.external_id}) was made for {_describe(item)}, but"
                        " QuickBooks has no invoice with that id. Either it was deleted,"
                        " or it was made in a different QuickBooks company, such as the"
                        " sandbox. While it is in my records, the daily check for paid"
                        " invoices fails.",
                        "If it was a test, remove my record of it with"
                        f" `uv run fops forget {item.id} --force`. If it was a real invoice"
                        " someone deleted, make it again in QuickBooks first.",
                        item.id,
                        about=record.external_id,
                    )
                )
                continue
            if held.item_id is not None and held.item_id != item.id:
                findings.append(
                    Finding(
                        FindingKind.INVOICE_FOR_ANOTHER_ITEM,
                        f"Invoice {held.number} (QuickBooks id {held.external_id}) is in"
                        f" my records for {_describe(item)}, but its note in QuickBooks"
                        f" says I made it for item {held.item_id}.",
                        "Check which work it is really for before anything else is sent.",
                        item.id,
                    )
                )
            if held.number and held.number != record.number:
                findings.append(
                    Finding(
                        FindingKind.INVOICE_RENUMBERED,
                        f"QuickBooks calls invoice {record.external_id} {held.number};"
                        f" my records call it {record.number} ({_describe(item)}).",
                        "Nothing to do if someone renamed it on purpose; the client sees"
                        " QuickBooks' number.",
                        item.id,
                        needs_attention=False,
                    )
                )
    return findings


# --- who holds an invoice number ---


def explain_invoice_number(
    looking: Looking, number: str, for_item: int | None = None
) -> list[Finding]:
    """Which invoice holds this number in QuickBooks, and whose work it is.

    `for_item` is the item that wants the number, so its own invoice is not
    reported as being in its way.
    """
    if not looking.accounting.can_look_up_invoices:
        return []
    try:
        holders = looking.accounting.invoices_numbered(number)
    except AccountingFailed as error:
        return [_unreachable(error)]
    if not holders:
        return [
            Finding(
                FindingKind.NUMBER_FREE,
                f"No invoice in QuickBooks is numbered {number}.",
                "",
                for_item,
                needs_attention=False,
            )
        ]
    items = _items_by_id(looking)
    return [_who_holds(holder, number, items, for_item) for holder in holders]


def _who_holds(
    holder: InvoiceLookup, number: str, items: dict[int, Item], for_item: int | None
) -> Finding:
    voided = holder.total_cents == 0
    shown = (
        f"QuickBooks invoice {number} (id {holder.external_id}"
        + (f", {holder.customer}" if holder.customer else "")
        + (f", dated {holder.issued}" if holder.issued else "")
        + (", voided" if voided else f", {_money(holder.total_cents)}")
        + ")"
    )
    delete = (
        f"If it is a leftover, delete invoice {number} in QuickBooks -- delete, not"
        ' void: a voided invoice keeps its number -- and reply "try again". Or reply'
        f' with another number, such as "use {number}-revised".'
    )
    if holder.item_id is None:
        return Finding(
            FindingKind.NUMBER_HELD_BY_HAND,
            f"{shown} was not made by me: it has no note naming one of my items, so"
            " someone made it by hand.",
            delete,
            for_item,
        )
    owner = items.get(holder.item_id)
    if owner is None:
        return Finding(
            FindingKind.NUMBER_HELD_BY_FORGOTTEN_ITEM,
            f"{shown} is one I made, for item {holder.item_id}, which is no longer in my"
            " records -- it was removed with `fops forget`. The invoice stayed in"
            " QuickBooks.",
            delete,
            for_item,
        )
    if owner.id == for_item:
        return Finding(
            FindingKind.NUMBER_HELD_BY_ANOTHER_ITEM,
            f"{shown} is this item's own invoice, already made.",
            "",
            for_item,
            needs_attention=False,
        )
    return Finding(
        FindingKind.NUMBER_HELD_BY_ANOTHER_ITEM,
        f"{shown} is the invoice I made for {_describe(owner)}.",
        "Two items want the same number. Check whether they are the same work: if"
        " one is a duplicate, forget it; if not, reply to the review with another"
        " number for this one.",
        for_item,
    )


# --- the inbox ---


def check_inbox(looking: Looking) -> list[Finding]:
    """Every email in the inbox, and whether the next run will read it.

    A run skips mail it has already stored, mail dated before the start date,
    and mail it has already read past, all without a word. This says which.
    """
    if looking.inbox is None:
        return []
    store = looking.store
    waiting = {message.message_id for message in store.unprocessed_messages()}
    items = store.list_items()
    findings: list[Finding] = []
    for entry in looking.inbox.inbox_listing(looking.mailbox_position):
        what = f'"{entry.subject}" from {entry.from_address} ({entry.received_at:%Y-%m-%d})'
        if store.has_message(entry.message_id):
            if entry.message_id in waiting:
                findings.append(
                    Finding(
                        FindingKind.EMAIL_WAITING_TO_BE_PROCESSED,
                        f"{what} is stored and will be dealt with on the next run.",
                        "",
                        needs_attention=False,
                    )
                )
                continue
            owners = [i for i in items if entry.message_id in store.message_ids_for_item(i.id)]
            if owners:
                forget = " and ".join(f"`fops forget {owner.id}`" for owner in owners)
                findings.append(
                    Finding(
                        FindingKind.EMAIL_ALREADY_HANDLED,
                        f"{what} has already been handled, for "
                        + "; ".join(_describe(owner) for owner in owners)
                        + ". I recognise it by its Message-ID and will not read it again.",
                        f"To put it through again: {forget} first (add --force if its"
                        " invoice went out), then move the email out of the inbox and"
                        " back in.",
                        owners[0].id,
                    )
                )
            else:
                findings.append(
                    Finding(
                        FindingKind.EMAIL_ALREADY_HANDLED,
                        f"{what} has already been handled (it was not a timesheet, or it"
                        " was a reply). I will not read it again.",
                        "Nothing to do; it is safe to move it out of the inbox.",
                    )
                )
            continue
        if not entry.on_or_after_start:
            since = f"{entry.received_at:%Y-%m-%d}"
            start = f" ({looking.mail_start_date})" if looking.mail_start_date else ""
            findings.append(
                Finding(
                    FindingKind.EMAIL_BEFORE_START_DATE,
                    f"{what} arrived before the mail start date{start}, so I ignore it."
                    " Moving an email between folders keeps the date it arrived.",
                    f"Run `uv run fops run --since {since}` once, or set MAIL_START_DATE"
                    " in .env to an earlier date.",
                )
            )
            continue
        if not entry.after_position:
            findings.append(
                Finding(
                    FindingKind.EMAIL_ALREADY_READ_PAST,
                    f"{what} is in the inbox, but I have already read past it. That"
                    " happens when an email is moved back in while I still had it on"
                    " record, then the record is removed.",
                    "Move it out of the inbox and back in again, or run"
                    f" `uv run fops run --since {entry.received_at:%Y-%m-%d}` once.",
                )
            )
            continue
        findings.append(
            Finding(
                FindingKind.EMAIL_WILL_BE_READ,
                f"{what} will be read on the next run.",
                "",
                needs_attention=False,
            )
        )
    return findings


# --- items ---


def check_items(looking: Looking, workbook: EngagementWorkbook) -> list[Finding]:
    """Items that no longer match the engagements, and engagements chasing too
    many periods at once."""
    on_list = {(row.consultant, row.client) for row in workbook.engagements}
    findings: list[Finding] = []
    waiting: dict[tuple[str, str], list[Item]] = defaultdict(list)
    for item in looking.store.list_items():
        if item.status in FINAL:
            continue
        if (item.consultant, item.client) not in on_list:
            sent = item.status is ItemStatus.INVOICE_SENT
            findings.append(
                Finding(
                    FindingKind.ITEM_NOT_ON_ENGAGEMENT_LIST,
                    f"{_describe(item).capitalize()} matches no engagement I know of"
                    f" today: nothing is {item.consultant} at {item.client}. It is"
                    " probably from testing, or from before the client's name changed.",
                    f"If it is a leftover, `uv run fops forget {item.id}"
                    + (" --force`" if sent else "`")
                    + ". It stays where it is otherwise.",
                    item.id,
                )
            )
            continue
        if item.status is ItemStatus.WAITING_FOR_TIMESHEET:
            waiting[(item.consultant, item.client)].append(item)
    starts = {(row.consultant, row.client): row.start_date for row in workbook.engagements}
    for (consultant, client), items in sorted(waiting.items()):
        if len(items) < MANY_WAITING:
            continue
        oldest = min(items, key=lambda item: item.period.start)
        start = starts.get((consultant, client))
        findings.append(
            Finding(
                FindingKind.MANY_PERIODS_WAITING,
                f"I am waiting for {len(items)} timesheets from {consultant} at {client},"
                f" the oldest for {format_period(oldest.period)}"
                + (f", because the engagement starts on {start}" if start else "")
                + ". If those months were billed by hand, the start date is too early.",
                "Fix the start date (`Start:` on the product in QuickBooks, or the"
                " engagement list), then forget the items for months that are not owed.",
                oldest.id,
            )
        )
    return findings


# --- one item, everything about it ---


@dataclass(frozen=True)
class ItemReport:
    item: Item
    facts: list[str]
    findings: list[Finding]


def describe_item(looking: Looking, item_id: int) -> ItemReport:
    """Everything the agent knows about one item, and what is wrong with it."""
    store = looking.store
    item = store.get_item(item_id)
    facts = [
        f"{item.consultant} at {item.client}, {format_period(item.period)}: {item.status.value}"
    ]
    if item.approved_hours is not None:
        facts.append(f"Approved hours: {item.approved_hours}")
    if item.invoice_amount is not None:
        facts.append(f"Invoice amount: {dollars(item.invoice_amount)}")
    for record in store.invoices_for_item(item.id):
        facts.append(
            f"Invoice {record.number} (QuickBooks id {record.external_id}): {record.status}"
        )
    for review in store.reviews_for_item(item.id):
        facts.append(f"Review {review.code} ({review.status}): {review.message}")
    for sent in store.outgoing_records():
        if sent.item_id == item.id:
            subject = sent.payload.get("subject", "")
            facts.append(f"Email {sent.kind}: {subject} ({sent.status})")
    for entry in store.audit_entries(item.id):
        facts.append(f"{entry.at:%Y-%m-%d %H:%M} {entry.what}")

    findings = [f for f in check_recorded_invoices(looking) if f.item_id == item.id]
    for review in store.open_reviews():
        if review.item_id != item.id:
            continue
        taken = _TAKEN_NUMBER.search(review.message)
        if taken is not None and not is_voided_number(taken.group(1)):
            findings += explain_invoice_number(looking, taken.group(1), item.id)
    return ItemReport(item=item, facts=facts, findings=findings)


def diagnose_everything(looking: Looking, workbook: EngagementWorkbook | None) -> list[Finding]:
    """Every check that needs no argument, in the order worth reading them."""
    findings = check_recorded_invoices(looking)
    for review in looking.store.open_reviews():
        taken = _TAKEN_NUMBER.search(review.message)
        if taken is not None:
            findings += explain_invoice_number(looking, taken.group(1), review.item_id)
    findings += check_inbox(looking)
    workbook = workbook or looking.workbook
    if workbook is not None:
        findings += check_items(looking, workbook)
    return findings
