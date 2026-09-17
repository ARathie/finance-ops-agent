"""The daily paid check, through a whole run on fakes."""

from datetime import date

from finance_ops_agent.application.context import Mode
from finance_ops_agent.application.paid_check import (
    LAST_PAID_CHECK_KEY,
    check_paid_invoices,
)
from finance_ops_agent.domain.statuses import ItemStatus
from tests.scenarios.conftest import PRIYA, ScenarioEnv, engagement_row, reading

AUG_START, AUG_END = date(2026, 8, 1), date(2026, 8, 31)


def sent_invoice(env: ScenarioEnv) -> tuple[int, str]:
    """Get to invoice_sent in automatic mode, and return the item and invoice."""
    env.mode = Mode.AUTO
    env.workbook.engagements[0] = engagement_row(2, **{"Send automatically": "yes"})
    env.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END))
    env.run()
    item = env.the_item()
    assert item.status is ItemStatus.INVOICE_SENT
    [invoice] = env.store.invoices_for_item(item.id)
    return item.id, invoice.external_id


class TestPaidCheck:
    def test_an_unpaid_invoice_leaves_the_item_alone(self, env: ScenarioEnv) -> None:
        item_id, _ = sent_invoice(env)
        env.run()
        assert env.store.get_item(item_id).status is ItemStatus.INVOICE_SENT

    def test_a_paid_invoice_makes_the_item_client_paid(self, env: ScenarioEnv) -> None:
        item_id, external_id = sent_invoice(env)
        env.accounting.paid.add(external_id)  # the client paid it

        deps = env.deps()
        check_paid_invoices(deps, env.report(), force=True)

        assert env.store.get_item(item_id).status is ItemStatus.CLIENT_PAID
        [invoice] = env.store.invoices_for_item(item_id)
        assert invoice.status == "paid"

    def test_the_check_runs_once_a_day(self, env: ScenarioEnv) -> None:
        item_id, external_id = sent_invoice(env)
        assert env.store.get_state(LAST_PAID_CHECK_KEY) == env.today.isoformat()

        # Paid after today's check already ran: nothing changes until tomorrow.
        env.accounting.paid.add(external_id)
        env.run()
        assert env.store.get_item(item_id).status is ItemStatus.INVOICE_SENT

        env.today = env.today.replace(day=env.today.day + 1)
        env.run()
        assert env.store.get_item(item_id).status is ItemStatus.CLIENT_PAID

    def test_a_paid_item_is_not_asked_about_again(self, env: ScenarioEnv) -> None:
        item_id, external_id = sent_invoice(env)
        env.accounting.paid.add(external_id)
        check_paid_invoices(env.deps(), env.report(), force=True)
        assert env.store.get_item(item_id).status is ItemStatus.CLIENT_PAID

        # client_paid is final: running again must not try to change it.
        check_paid_invoices(env.deps(), env.report(), force=True)
        assert env.store.get_item(item_id).status is ItemStatus.CLIENT_PAID

    def test_nothing_sent_means_nothing_to_ask(self, env: ScenarioEnv) -> None:
        env.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END))
        env.run()  # dry run: ready, no invoice
        assert env.the_item().status is ItemStatus.READY
        assert env.accounting.asked == []


class TestWhenQuickBooksCannotAnswer:
    """Not knowing whether a client has paid is worth telling Kevin about, and
    is never worth stopping the run for (docs/timesheet-checks.md)."""

    def test_a_refusal_becomes_a_review_and_the_run_carries_on(self, env: ScenarioEnv) -> None:
        from finance_ops_agent.ports.accounting import AccountingFailed

        item_id, _ = sent_invoice(env)
        env.accounting.fail_with = AccountingFailed("service unavailable")

        report = env.report()
        check_paid_invoices(env.deps(), report, force=True)

        review = next(r for r in env.store.open_reviews() if r.code == "QUICKBOOKS_FAILED")
        assert "which invoices have been paid" in review.message
        assert "service unavailable" in review.message
        assert env.store.get_item(item_id).status is ItemStatus.INVOICE_SENT

    def test_the_day_is_not_marked_done_so_the_next_run_asks_again(self, env: ScenarioEnv) -> None:
        from finance_ops_agent.ports.accounting import AccountingNeedsReconnect

        item_id, external_id = sent_invoice(env)
        env.store.set_state(LAST_PAID_CHECK_KEY, "")
        env.accounting.fail_with = AccountingNeedsReconnect("the refresh token has expired")

        check_paid_invoices(env.deps(), env.report(), force=True)
        assert env.store.get_state(LAST_PAID_CHECK_KEY) != env.today.isoformat()
        assert any(r.code == "QUICKBOOKS_RECONNECT" for r in env.store.open_reviews())

        env.accounting.fail_with = None  # Kevin reconnected
        env.accounting.paid.add(external_id)
        check_paid_invoices(env.deps(), env.report(), force=True)
        assert env.store.get_item(item_id).status is ItemStatus.CLIENT_PAID
