"""A run asks QuickBooks only about what is in front of it (decision 54).

The agent runs every 15 minutes, and most of those runs find an empty mailbox.
Building the engagement list from QuickBooks, and asking for every engagement's
product to confirm an item already in hand, used to happen on every one of them.
Now the engagement list is built only when mail has arrived, a message is still
waiting, or today's look for ended billing periods is still to do.
"""

from collections import Counter
from datetime import date

from finance_ops_agent.adapters.fakes.accounting import FakeAccounting
from finance_ops_agent.application.run import LAST_EXPECTED_CHECK_KEY
from finance_ops_agent.domain.statuses import ItemStatus
from finance_ops_agent.ports.accounting import (
    AccountingFailed,
    EngagementRates,
)
from tests.scenarios.conftest import PRIYA, ScenarioEnv, engagement_row, reading

AUG_START, AUG_END = date(2026, 8, 1), date(2026, 8, 31)


def counting(env: ScenarioEnv) -> FakeAccounting:
    accounting = env.accounting
    for consultant in ("Priya Shah", "Dana Cruz"):
        accounting.rates[(consultant, "Acme Corp")] = EngagementRates(
            ref=f"ref-{consultant}", bill_rate_cents=14_000, pay_rate_cents=10_000, payee=""
        )
    return accounting


def waiting_periods(env: ScenarioEnv) -> set[tuple[str, date]]:
    return {
        (item.consultant, item.period.start)
        for item in env.store.list_items()
        if item.status is ItemStatus.WAITING_FOR_TIMESHEET
    }


class TestAQuietRun:
    def test_asks_quickbooks_nothing(self, env: ScenarioEnv) -> None:
        accounting = counting(env)
        env.run()  # the day's first run looks for ended periods
        accounting.calls.clear()

        env.run()
        env.run()

        assert accounting.calls == Counter()

    def test_asks_nothing_when_quickbooks_holds_the_engagements_too(self, env: ScenarioEnv) -> None:
        env.engagements_from = "quickbooks"
        accounting = counting(env)
        env.run()
        accounting.calls.clear()

        env.run()

        assert accounting.calls == Counter()

    def test_still_catches_a_broken_spreadsheet_edit(self, env: ScenarioEnv) -> None:
        """Reading the spreadsheet asks QuickBooks nothing, so it is still read
        every run, and a bad row is still Kevin's to hear about within 15
        minutes rather than tomorrow."""
        counting(env)
        env.run()
        env.workbook.engagements[0] = engagement_row(2, **{"Bill rate": "lots"})

        report = env.run()

        assert report.reviews_opened == 1


class TestTheDailyLook:
    def test_the_first_run_of_the_day_finds_a_period_that_ended(self, env: ScenarioEnv) -> None:
        counting(env)
        env.run()
        assert waiting_periods(env) == {("Priya Shah", AUG_START)}

        env.today = date(2026, 10, 1)
        env.run()

        assert waiting_periods(env) == {
            ("Priya Shah", AUG_START),
            ("Priya Shah", date(2026, 9, 1)),
            ("Dana Cruz", date(2026, 9, 1)),
        }

    def test_it_is_done_once_a_day(self, env: ScenarioEnv) -> None:
        accounting = counting(env)
        env.run()
        env.run()
        assert accounting.calls["engagements"] == 1
        assert env.store.get_state(LAST_EXPECTED_CHECK_KEY) == env.today.isoformat()

    def test_quickbooks_down_means_the_next_run_looks_again(self, env: ScenarioEnv) -> None:
        """An outage must not hide an ended period until tomorrow."""
        accounting = counting(env)
        accounting.fail_with = AccountingFailed("QuickBooks is unreachable")
        env.run()
        assert env.store.get_state(LAST_EXPECTED_CHECK_KEY) is None

        accounting.fail_with = None
        accounting.calls.clear()
        env.run()

        assert accounting.calls["engagements"] == 1
        assert env.store.get_state(LAST_EXPECTED_CHECK_KEY) == env.today.isoformat()

    def test_a_stand_in_engagement_list_means_the_next_run_looks_again(
        self, env: ScenarioEnv
    ) -> None:
        """QuickBooks mode that fell back to the spreadsheet has not really
        looked at what QuickBooks holds."""
        env.engagements_from = "quickbooks"
        accounting = counting(env)
        accounting.fail_with = AccountingFailed("QuickBooks is unreachable")
        env.run()

        assert env.store.get_state(LAST_EXPECTED_CHECK_KEY) is None


class TestMailOnAQuietDay:
    def test_a_timesheet_arriving_later_in_the_day_is_handled(self, env: ScenarioEnv) -> None:
        counting(env)
        env.run()
        env.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END))

        report = env.run()

        assert report.timesheets_processed == 1
        assert waiting_periods(env) == set()

    def test_only_the_engagement_in_hand_is_asked_about(self, env: ScenarioEnv) -> None:
        """August is already waiting for Priya, so her timesheet joins it by
        name. Her rates are asked for again, because they are taken when the
        timesheet is read (decision 43); nobody else's are."""
        accounting = counting(env)
        env.run()
        accounting.calls.clear()
        env.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END))

        env.run()

        assert accounting.calls["engagement_rates"] == 1
        assert accounting.calls["engagements"] == 0
