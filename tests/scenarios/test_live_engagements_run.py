"""QuickBooks decides which engagements are live, through a whole run.

The engagement list's `Active` column used to decide this on its own. The
engagements live in QuickBooks now (docs/decisions.md #42), so making a product
inactive is how Kevin says one has finished -- and an engagement QuickBooks has
that the list cannot schedule is a review, not a guess.
"""

from dataclasses import replace
from datetime import date

import pytest

from finance_ops_agent.domain.periods import BillingPeriod
from finance_ops_agent.domain.statuses import ItemStatus
from finance_ops_agent.ports.accounting import AccountingEngagement, AccountingFailed
from tests.scenarios.conftest import ScenarioEnv

AUG_START, AUG_END = date(2026, 8, 1), date(2026, 8, 31)


def listed(consultant: str, client: str = "Acme Corp") -> AccountingEngagement:
    return AccountingEngagement(
        ref=f"ref-{consultant}",
        consultant=consultant,
        client=client,
        bill_rate_cents=14_000,
        pay_rate_cents=10_000,
        payee=consultant,
    )


def open_codes(env: ScenarioEnv) -> set[str]:
    return {review.code for review in env.store.open_reviews()}


def waiting_for(env: ScenarioEnv) -> set[str]:
    return {
        item.consultant
        for item in env.store.list_items()
        if item.status is ItemStatus.WAITING_FOR_TIMESHEET
    }


class TestQuickBooksSaysAnEngagementHasFinished:
    def test_no_new_periods_are_expected_for_a_product_that_is_gone(self, env: ScenarioEnv) -> None:
        """Dana's row is still active on the list; her product is not in
        QuickBooks, so no timesheet is expected."""
        env.accounting.live = [listed("Priya Shah")]
        env.run()
        assert waiting_for(env) == {"Priya Shah"}

    def test_it_is_said_in_the_run_report_rather_than_left_silent(self, env: ScenarioEnv) -> None:
        env.accounting.live = [listed("Priya Shah")]
        report = env.run()
        assert any(
            "Dana Cruz" in line and "not live in QuickBooks" in line for line in report.lines
        )

    def test_it_is_not_also_a_review(self, env: ScenarioEnv) -> None:
        """`fops doctor` fails its products check on exactly this, before any
        timesheet is due. Saying it twice would train Kevin to skim both."""
        env.accounting.live = [listed("Priya Shah")]
        env.run()
        assert open_codes(env) == set()

    def test_work_already_in_hand_is_not_abandoned(self, env: ScenarioEnv) -> None:
        """Inactive means "expect no more timesheets", never "drop what is
        already waiting"."""
        env.run()  # both engagements live; both get an expected item
        before = {item.id for item in env.store.list_items()}
        assert before

        env.accounting.live = [listed("Priya Shah")]
        env.run()
        assert {item.id for item in env.store.list_items()} == before


class TestQuickBooksHasAnEngagementTheListCannotSchedule:
    def test_kevin_is_asked_rather_than_a_schedule_being_guessed(self, env: ScenarioEnv) -> None:
        env.accounting.live = [listed("Priya Shah"), listed("Dana Cruz"), listed("Sam Okafor")]
        env.run()
        assert open_codes(env) == {"LIST_ROW_PROBLEM"}
        said = " ".join(review.message for review in env.store.open_reviews())
        assert "Sam Okafor at Acme Corp" in said
        assert "how often to expect a timesheet" in said

    def test_the_engagements_that_do_have_rows_still_run(self, env: ScenarioEnv) -> None:
        env.accounting.live = [listed("Priya Shah"), listed("Dana Cruz"), listed("Sam Okafor")]
        env.run()
        assert "Priya Shah" in waiting_for(env)

    def test_it_is_asked_once_not_once_a_run(self, env: ScenarioEnv) -> None:
        env.accounting.live = [listed("Priya Shah"), listed("Dana Cruz"), listed("Sam Okafor")]
        env.run()
        env.run()
        assert len(env.store.open_reviews()) == 1


class TestWhenQuickBooksCannotAnswer:
    def test_an_accounting_system_that_is_down_does_not_stop_the_run(
        self, env: ScenarioEnv
    ) -> None:
        """Being unable to ask must never look like Icon having stopped
        working: the engagement list decides, as it did before."""
        env.accounting.fail_with = AccountingFailed("QuickBooks is unreachable")
        env.run()
        assert waiting_for(env) == {"Priya Shah"}  # Dana's engagement starts in September

    def test_nothing_to_say_leaves_the_engagement_list_in_charge(self, env: ScenarioEnv) -> None:
        """Manual mode, and a company whose products are not filled in yet,
        both answer with an empty list."""
        env.accounting.live = []
        env.run()
        assert waiting_for(env) == {"Priya Shah"}


@pytest.mark.parametrize("category", ["Acme Corp", "Acme Corporation"])
def test_the_client_is_matched_under_any_of_its_names(env: ScenarioEnv, category: str) -> None:
    """The category in QuickBooks is often the client's legal name, and the
    list's rows use its short name."""
    env.accounting.live = [listed("Priya Shah", category)]
    env.run()
    assert waiting_for(env) == {"Priya Shah"}
    assert open_codes(env) == set()


def test_a_row_marked_inactive_by_hand_is_overridden_by_a_live_product(
    env: ScenarioEnv,
) -> None:
    """QuickBooks is the switch now. The row is still what says when to bill,
    so its schedule is read even though its own flag says inactive."""
    from tests.scenarios.conftest import engagement_row

    env.workbook = replace(env.workbook, engagements=[engagement_row(2, Active="no")])
    env.accounting.live = [listed("Priya Shah")]
    env.run()
    assert (
        env.store.find_item("Priya Shah", "Acme Corp", BillingPeriod(AUG_START, AUG_END))
        is not None
    )
