"""`fops qbo-test-invoice`: prove the invoice-creating path against a real company.

The ordinary flow cannot be used to test this. `dry_run` creates nothing, on
purpose, so the only way an invoice reaches QuickBooks is Kevin approving one
-- and approving sends the billing email to whatever address the engagement
list holds. This command is the create path with nothing attached to it: no
mailbox, no email, nothing that can reach a client, and by default no invoice
left behind either.

It goes the whole way through the real code: the engagement list, the billing
period, the rate row in force, the invoice number, the customer and product
lookups, the create, the checks on what came back, and the PDF. What it skips
is everything after the invoice exists.

The invoice is deleted afterwards so a company Icon is not yet using stays
clean, which also puts the number back. `--cleanup void` leaves the voided
record instead (a real correction always voids, never deletes), and
`--cleanup keep` leaves the invoice there to be looked at.
"""

from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from finance_ops_agent.domain.invoice_numbers import invoice_number
from finance_ops_agent.domain.invoices import build_invoice
from finance_ops_agent.domain.money import Hours, Money, invoice_amount
from finance_ops_agent.domain.periods import period_containing
from finance_ops_agent.domain.statuses import ItemStatus

if TYPE_CHECKING:
    from finance_ops_agent.adapters.quickbooks.online import QuickBooksOnline
    from finance_ops_agent.config import Config
    from finance_ops_agent.domain.engagements import EngagementWorkbook
    from finance_ops_agent.domain.items import Item

MAX_NUMBER_ATTEMPTS = 20
DUPLICATE_MARKERS = ("6140", "Duplicate Document Number")


class InvoiceTestFailed(Exception):
    """Something stopped the test; the message says what and what to do."""


def _is_duplicate_number(error: Exception) -> bool:
    message = str(error)
    return any(marker in message for marker in DUPLICATE_MARKERS)


def build_test_item(
    workbook: "EngagementWorkbook",
    consultant: str,
    client: str,
    period_end: date,
    hours: Hours,
    item_id: int,
) -> "Item":
    """The same Item the agent would have built from a timesheet, without one.

    Everything comes from the engagement list exactly as it does in a real run,
    so a workbook that would not have invoiced this consultant does not invoice
    them here either.
    """
    from finance_ops_agent.application.run import build_snapshot
    from finance_ops_agent.domain import checks
    from finance_ops_agent.domain.items import Item

    rows = [
        row
        for row in workbook.engagements
        if checks.names_match(row.consultant, consultant) and checks.names_match(row.client, client)
    ]
    if not rows:
        known = sorted({f"{row.consultant} at {row.client}" for row in workbook.engagements})
        raise InvoiceTestFailed(
            f"The engagement list has no row for {consultant} at {client}."
            " It has: " + ("; ".join(known) if known else "nothing")
        )

    first = min(row.start_date for row in rows)
    last = max((row.end_date for row in rows if row.end_date is not None), default=None)
    period = period_containing(
        period_end,
        rows[0].billing_schedule,
        engagement_start=first,
        engagement_end=last,
        first_period_start=rows[0].first_period_start,
    )
    rate_row, findings = checks.rate_row_in_force(rows, period)
    if rate_row is None:
        raise InvoiceTestFailed(
            "; ".join(finding.message for finding in findings)
            or f"The engagement list has no rate for {consultant} at {client} in this period."
        )
    snapshot = build_snapshot(workbook, rate_row)
    if snapshot is None:
        raise InvoiceTestFailed(
            f"I could not put together the engagement for {consultant} at {client}."
            " Run `fops doctor` -- a row on the Clients, Consultants, or Vendors"
            " sheet is probably incomplete."
        )
    bill_rate = Money(snapshot.bill_rate_cents)
    return Item(
        id=item_id,
        consultant=rate_row.consultant,
        client=rate_row.client,
        period=period,
        status=ItemStatus.READY,
        snapshot=snapshot,
        approved_hours=hours,
        invoice_amount=invoice_amount(hours, bill_rate),
        amount_owed=Money(0),  # nothing here pays anyone; the payment path is untouched
    )


def create_and_check(
    accounting: "QuickBooksOnline",
    item: "Item",
    today: date,
    announce: Callable[[str], None] = print,
) -> tuple[str, bytes]:
    """Create the invoice, stepping past numbers QuickBooks already has.

    A test invoice that was voided rather than deleted keeps its number, so a
    second run of the same consultant and period would collide. That is the
    same `-2` the real flow uses for a replacement, worked out here against
    QuickBooks itself rather than against the agent's own records.
    """
    from finance_ops_agent.adapters.quickbooks.client import QuickBooksFailed

    assert item.approved_hours is not None
    last_error: Exception | None = None
    for attempt in range(1, MAX_NUMBER_ATTEMPTS + 1):
        number = invoice_number(
            item.period.end,
            item.snapshot.client_invoice_code,
            item.snapshot.consultant_code,
            attempt,
        )
        invoice = replace(build_invoice(item, number, today), number=number)
        announce(f"  creating invoice {number} ...")
        try:
            created = accounting.create_invoice(invoice, item.id)
        except QuickBooksFailed as error:
            if not _is_duplicate_number(error):
                raise
            announce(f"  QuickBooks already has {number}; trying the next one")
            last_error = error
            continue
        return created.external_id, created.pdf
    raise InvoiceTestFailed(f"every number I tried is already in QuickBooks: {last_error}")


def run_test_invoice(
    config: "Config",
    consultant: str,
    client: str,
    period_end: date,
    hours: Hours,
    cleanup: str,
    pdf_path: Path | None,
    item_id: int | None = None,
) -> int:
    from finance_ops_agent.adapters.excel.engagement_list import ExcelEngagementList
    from finance_ops_agent.adapters.quickbooks.client import (
        QuickBooksClient,
        QuickBooksFailed,
        QuickBooksReconnect,
    )
    from finance_ops_agent.adapters.quickbooks.online import QuickBooksOnline
    from finance_ops_agent.adapters.quickbooks.tokens import NotConnected, TokenStore
    from finance_ops_agent.config import MissingSettingError, QuickBooksSettings
    from finance_ops_agent.domain.engagements import parse_workbook

    # A marker QuickBooks keeps in the invoice's private note. Different every
    # run by default, so a kept invoice from an earlier run is never mistaken
    # for this one's.
    marker = item_id if item_id is not None else int(datetime.now().timestamp()) % 1_000_000  # noqa: DTZ005

    try:
        settings = QuickBooksSettings.from_env()
    except MissingSettingError as error:
        print(f"QuickBooks is not configured: {error}")
        return 1
    store = TokenStore(config.qbo_token_path)
    # Not `client`: in this file that is the company being invoiced.
    connection = QuickBooksClient(store, settings.client_id, settings.client_secret)
    today = date.today()  # noqa: DTZ011 - a test run, not a billing decision
    accounting = QuickBooksOnline(connection, today)

    try:
        tokens = store.load()
    except NotConnected as error:
        print(f"{error}")
        return 1
    print(f"Company {tokens.realm_id} ({tokens.environment}). Nothing here sends any email.")

    try:
        workbook = parse_workbook(ExcelEngagementList(config.engagement_list).load())
        item = build_test_item(workbook, consultant, client, period_end, hours, marker)
        print(
            f"  {item.consultant} at {item.client},"
            f" {item.period.start} to {item.period.end}, {item.approved_hours} hours"
        )
        print(f"  marker in the invoice's private note: fops item {marker}")
        quickbooks_id, pdf = create_and_check(accounting, item, today)
    except (InvoiceTestFailed, QuickBooksFailed, QuickBooksReconnect) as error:
        print(f"\nIt did not work: {error}")
        return 1

    assert item.invoice_amount is not None
    print(f"  created, and QuickBooks agrees it comes to {item.invoice_amount}")

    destination = pdf_path or config.data_dir / f"qbo-test-invoice-{quickbooks_id}.pdf"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(pdf)
    print(f"  the PDF QuickBooks rendered is at {destination} ({len(pdf)} bytes)")

    if cleanup == "keep":
        print(f"\nLeft invoice {quickbooks_id} in QuickBooks, as asked.")
        return 0
    try:
        if cleanup == "void":
            accounting.cancel_invoice(quickbooks_id)
            print(f"\nVoided invoice {quickbooks_id}; its number stays used.")
        else:
            accounting.delete_invoice(quickbooks_id)
            print(f"\nDeleted invoice {quickbooks_id}; the company is as it was.")
    except (QuickBooksFailed, QuickBooksReconnect) as error:
        print(
            f"\nThe invoice was created but I could not clean it up: {error}\n"
            f"Remove invoice {quickbooks_id} in QuickBooks by hand."
        )
        return 1
    return 0
