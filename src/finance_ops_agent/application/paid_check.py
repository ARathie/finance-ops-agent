"""The once-a-day question: has the client paid?

Only invoices the agent created and has not seen paid are asked about. Balance
zero makes the item client_paid; a partial payment leaves the status alone. The
agent never records a payment in QuickBooks — Kevin or the bookkeeper does that,
as today.
"""

from finance_ops_agent.application.context import RunDeps, RunReport
from finance_ops_agent.domain.review import ReviewCode
from finance_ops_agent.domain.statuses import ItemStatus
from finance_ops_agent.ports.accounting import AccountingFailed, AccountingNeedsReconnect

LAST_PAID_CHECK_KEY = "last_paid_check"


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
    try:
        paid = deps.accounting.paid_status(list(unpaid))
    except AccountingFailed as error:
        # Not knowing whether a client has paid is worth telling Kevin about,
        # but it is not worth stopping the run for, and the day is deliberately
        # not marked done so the next run asks again.
        reconnect = isinstance(error, AccountingNeedsReconnect)
        if reconnect:
            report.quickbooks_unavailable = True
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
        return
    for external_id, is_paid in paid.items():
        if not is_paid:
            continue
        item_id = unpaid[external_id]
        deps.store.set_invoice_status(external_id, "paid")
        item = deps.store.get_item(item_id)
        if item.status is ItemStatus.INVOICE_SENT:
            deps.store.change_status(item.id, ItemStatus.CLIENT_PAID, {"on": today})
            report.note(f"the client paid: {item.consultant} at {item.client}")
    deps.store.set_state(LAST_PAID_CHECK_KEY, today)
