from datetime import date, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from finance_ops_agent.domain.periods import (
    BillingPeriod,
    BillingSchedule,
    billing_periods,
    period_containing,
)


def test_period_start_after_end_raises() -> None:
    with pytest.raises(ValueError):
        BillingPeriod(date(2026, 8, 2), date(2026, 8, 1))


def test_covers() -> None:
    period = BillingPeriod(date(2026, 8, 1), date(2026, 8, 31))
    assert period.covers(date(2026, 8, 1))
    assert period.covers(date(2026, 8, 31))
    assert not period.covers(date(2026, 9, 1))


class TestMonthly:
    def test_full_months(self) -> None:
        periods = billing_periods(
            BillingSchedule.MONTHLY, date(2026, 2, 1), until=date(2026, 4, 30)
        )
        assert periods == [
            BillingPeriod(date(2026, 2, 1), date(2026, 2, 28)),
            BillingPeriod(date(2026, 3, 1), date(2026, 3, 31)),
            BillingPeriod(date(2026, 4, 1), date(2026, 4, 30)),
        ]

    def test_first_period_cut_short_at_engagement_start(self) -> None:
        periods = billing_periods(
            BillingSchedule.MONTHLY, date(2026, 2, 10), until=date(2026, 3, 31)
        )
        assert periods[0] == BillingPeriod(date(2026, 2, 10), date(2026, 2, 28))

    def test_last_period_cut_short_at_engagement_end(self) -> None:
        periods = billing_periods(
            BillingSchedule.MONTHLY,
            date(2026, 2, 1),
            engagement_end=date(2026, 4, 15),
            until=date(2026, 12, 31),
        )
        assert periods[-1] == BillingPeriod(date(2026, 4, 1), date(2026, 4, 15))

    def test_leap_february(self) -> None:
        period = period_containing(date(2028, 2, 15), BillingSchedule.MONTHLY, date(2028, 1, 1))
        assert period == BillingPeriod(date(2028, 2, 1), date(2028, 2, 29))


class TestTwiceAMonth:
    def test_halves_of_a_month(self) -> None:
        periods = billing_periods(
            BillingSchedule.TWICE_A_MONTH, date(2026, 8, 1), until=date(2026, 8, 31)
        )
        assert periods == [
            BillingPeriod(date(2026, 8, 1), date(2026, 8, 15)),
            BillingPeriod(date(2026, 8, 16), date(2026, 8, 31)),
        ]

    def test_day_15_is_in_the_first_half_and_16_in_the_second(self) -> None:
        first = period_containing(
            date(2026, 8, 15), BillingSchedule.TWICE_A_MONTH, date(2026, 1, 1)
        )
        second = period_containing(
            date(2026, 8, 16), BillingSchedule.TWICE_A_MONTH, date(2026, 1, 1)
        )
        assert first.end == date(2026, 8, 15)
        assert second.start == date(2026, 8, 16)

    def test_partial_first_and_last(self) -> None:
        periods = billing_periods(
            BillingSchedule.TWICE_A_MONTH,
            date(2026, 8, 10),
            engagement_end=date(2026, 8, 20),
            until=date(2026, 12, 31),
        )
        assert periods == [
            BillingPeriod(date(2026, 8, 10), date(2026, 8, 15)),
            BillingPeriod(date(2026, 8, 16), date(2026, 8, 20)),
        ]


class TestEveryTwoWeeks:
    ANCHOR = date(2026, 2, 2)  # a Monday

    def test_fourteen_day_periods_from_the_anchor(self) -> None:
        periods = billing_periods(
            BillingSchedule.EVERY_TWO_WEEKS,
            self.ANCHOR,
            first_period_start=self.ANCHOR,
            until=date(2026, 3, 1),
        )
        assert periods == [
            BillingPeriod(date(2026, 2, 2), date(2026, 2, 15)),
            BillingPeriod(date(2026, 2, 16), date(2026, 3, 1)),
        ]

    def test_first_period_cut_short_when_engagement_starts_mid_period(self) -> None:
        period = period_containing(
            date(2026, 2, 10),
            BillingSchedule.EVERY_TWO_WEEKS,
            date(2026, 2, 5),
            first_period_start=self.ANCHOR,
        )
        assert period == BillingPeriod(date(2026, 2, 5), date(2026, 2, 15))

    def test_anchor_can_be_any_period_start_even_a_later_one(self) -> None:
        # "the first day of any one period, so the agent can work out the rest"
        period = period_containing(
            date(2026, 2, 10),
            BillingSchedule.EVERY_TWO_WEEKS,
            date(2026, 2, 2),
            first_period_start=date(2026, 3, 2),
        )
        assert period == BillingPeriod(date(2026, 2, 2), date(2026, 2, 15))

    def test_needs_a_first_period_start(self) -> None:
        with pytest.raises(ValueError):
            period_containing(date(2026, 2, 10), BillingSchedule.EVERY_TWO_WEEKS, date(2026, 2, 2))


class TestWeekly:
    ANCHOR = date(2026, 2, 2)

    def test_seven_day_periods(self) -> None:
        periods = billing_periods(
            BillingSchedule.WEEKLY,
            self.ANCHOR,
            first_period_start=self.ANCHOR,
            until=date(2026, 2, 15),
        )
        assert periods == [
            BillingPeriod(date(2026, 2, 2), date(2026, 2, 8)),
            BillingPeriod(date(2026, 2, 9), date(2026, 2, 15)),
        ]

    def test_partial_first_and_last(self) -> None:
        periods = billing_periods(
            BillingSchedule.WEEKLY,
            date(2026, 2, 4),
            engagement_end=date(2026, 2, 17),
            first_period_start=self.ANCHOR,
            until=date(2026, 12, 31),
        )
        assert periods[0] == BillingPeriod(date(2026, 2, 4), date(2026, 2, 8))
        assert periods[-1] == BillingPeriod(date(2026, 2, 16), date(2026, 2, 17))

    def test_needs_a_first_period_start(self) -> None:
        with pytest.raises(ValueError):
            period_containing(date(2026, 2, 10), BillingSchedule.WEEKLY, date(2026, 2, 2))


class TestEngagementBounds:
    def test_day_before_engagement_raises(self) -> None:
        with pytest.raises(ValueError):
            period_containing(date(2026, 1, 31), BillingSchedule.MONTHLY, date(2026, 2, 1))

    def test_day_after_engagement_raises(self) -> None:
        with pytest.raises(ValueError):
            period_containing(
                date(2026, 5, 1),
                BillingSchedule.MONTHLY,
                date(2026, 2, 1),
                engagement_end=date(2026, 4, 30),
            )

    def test_no_periods_when_engagement_starts_after_until(self) -> None:
        assert (
            billing_periods(BillingSchedule.MONTHLY, date(2026, 6, 1), until=date(2026, 5, 1)) == []
        )


@given(
    schedule=st.sampled_from(list(BillingSchedule)),
    engagement_start=st.dates(date(2020, 1, 1), date(2030, 12, 31)),
    length_days=st.integers(min_value=0, max_value=400),
    anchor_offset_days=st.integers(min_value=-100, max_value=100),
)
def test_periods_are_contiguous_and_cover_the_engagement(
    schedule: BillingSchedule,
    engagement_start: date,
    length_days: int,
    anchor_offset_days: int,
) -> None:
    engagement_end = engagement_start + timedelta(days=length_days)
    periods = billing_periods(
        schedule,
        engagement_start,
        engagement_end=engagement_end,
        first_period_start=engagement_start + timedelta(days=anchor_offset_days),
        until=date(2033, 12, 31),
    )
    assert periods[0].start == engagement_start
    assert periods[-1].end == engagement_end
    for earlier, later in zip(periods, periods[1:], strict=False):
        assert later.start == earlier.end + timedelta(days=1)
