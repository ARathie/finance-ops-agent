"""The once-a-day question: has the client paid?

Only invoices the agent created and has not seen paid are asked about. Balance
zero makes the item client_paid; a partial payment leaves the status alone. The
agent never records a payment in QuickBooks — Kevin or the bookkeeper does that,
as today.

Each invoice is asked about on its own (decision 56). Before, one invoice
QuickBooks did not have -- made in the sandbox, or deleted by hand -- failed
the question for every invoice, on every run, with nothing to say which one.
Now that invoice gets a review of its own naming it and the fix, and the rest
are still checked.
"""

from finance_ops_agent.application.context import RunDeps, RunReport
from finance_ops_agent.application.diagnosis import FindingKind, check_recorded_invoices, looking_at
from finance_ops_agent.application.outgoing import enqueue_email
from finance_ops_agent.domain import emails
from finance_ops_agent.domain.review import ReviewCode
from finance_ops_agent.domain.statuses import ItemStatus
from finance_ops_agent.ports.accounting import AccountingFailed, AccountingNeedsReconnect

LAST_PAID_CHECK_KEY = "last_paid_check"


def _missing_invoices(deps: RunDeps, report: RunReport, failed: dict[str, int]) -> set[str]:
    """Which of the invoices that failed QuickBooks does not have at all, each
    raised as its own review and emailed. The ones left over failed for some
    other reason."""
    if not deps.accounting.can_look_up_invoices:
        return set()
    missing: set[str] = set()
    for finding in check_recorded_invoices(looking_at(deps)):
        if finding.kind is not FindingKind.INVOICE_NOT_IN_ACCOUNTING:
            continue
        if finding.about not in failed or finding.item_id is None:
            continue
        missing.add(finding.about)
        message = f"{finding.what} {finding.next_step}"
        if deps.store.open_review(finding.item_id, ReviewCode.QUICKBOOKS_FAILED.value, message):
            report.reviews_opened += 1
            item = deps.store.get_item(finding.item_id)
            email = emails.needs_review(
                deps.settings.admin_email, f"{item.consultant} — {item.client}", [message]
            )
            enqueue_email(
                deps, "review_email", f"review:missing-invoice:{finding.about}", item.id, email
            )
    return missing


def check_paid_invoices(deps: RunDeps, report: RunReport, force: bool = False) -> None:
    today = deps.clock.today().isoformat()
    if not force and deps.store.get_state(LAST_PAID_CHECK_KEY) == today:
        return  # once a day is enough
    unpaid: dict[str, int] = {}
    for item in deps.store.list_items():
        if item.status is not ItemStatus.INVOICE_SENT:
            continue
        for record in deps.store.invoices_for_item(item.id):
            if record.status in ("sent", "created"):
                unpaid[record.external_id] = item.id
    if not unpaid:
        deps.store.set_state(LAST_PAID_CHECK_KEY, today)
        return
    paid: dict[str, bool] = {}
    failed: dict[str, int] = {}
    last_error: AccountingFailed | None = None
    for external_id, item_id in unpaid.items():
        try:
            paid.update(deps.accounting.paid_status([external_id]))
        except AccountingNeedsReconnect as error:
            # Every other invoice would fail the same way; say it once.
            report.quickbooks_unavailable = True
            _could_not_ask(deps, report, error, reconnect=True)
            return
        except AccountingFailed as error:
            failed[external_id] = item_id
            last_error = error
    for external_id, is_paid in paid.items():
        if not is_paid:
            continue
        item_id = unpaid[external_id]
        deps.store.set_invoice_status(external_id, "paid")
        item = deps.store.get_item(item_id)
        if item.status is ItemStatus.INVOICE_SENT:
            deps.store.change_status(item.id, ItemStatus.CLIENT_PAID, {"on": today})
            report.note(f"the client paid: {item.consultant} at {item.client}")
    if failed:
        missing = _missing_invoices(deps, report, failed)
        for external_id in sorted(missing):
            report.note(f"QuickBooks has no invoice {external_id}; Kevin has a review naming it")
        if set(failed) - missing:
            # Not knowing is worth telling Kevin about, not worth stopping the
            # run for, and the day is not marked done so the next run asks again.
            assert last_error is not None
            _could_not_ask(deps, report, last_error, reconnect=False)
            return
    deps.store.set_state(LAST_PAID_CHECK_KEY, today)


def _could_not_ask(
    deps: RunDeps, report: RunReport, error: AccountingFailed, reconnect: bool
) -> None:
    code = ReviewCode.QUICKBOOKS_RECONNECT if reconnect else ReviewCode.QUICKBOOKS_FAILED
    opened = deps.store.open_review(
        None,
        code.value,
        "I could not ask QuickBooks which invoices have been paid, so the"
        " tracking sheet may be behind. Nothing else is affected."
        + (" Run `fops qbo-connect` to reconnect." if reconnect else "")
        + f" QuickBooks said: {error}",
    )
    if opened:
        report.reviews_opened += 1
    report.note(f"could not check paid invoices: {error}")
