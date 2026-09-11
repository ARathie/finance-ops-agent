"""Working days: weekdays that are not public holidays (decision 28).

Icon's consultants are not expected to work federal holidays, so a full month
comes to eight hours for each working day. The case that prompted it: the last
week of May 2025 shows 32 hours, because Memorial Day fell on Monday 26 May.
"""

from datetime import date

import pytest

from finance_ops_agent.domain.holidays import federal_holidays, is_holiday, working_days


def test_memorial_day_is_the_last_monday_in_may() -> None:
    assert is_holiday(date(2025, 5, 26))
    assert is_holiday(date(2026, 5, 25))


def test_the_three_real_months_expect_exactly_what_was_worked() -> None:
    """Each real timesheet comes to 100% of its working days at eight hours,
    which is the evidence that consultants are not working holidays."""
    assert working_days(date(2025, 5, 1), date(2025, 5, 31)) * 8 == 168
    assert working_days(date(2026, 7, 1), date(2026, 7, 31)) * 8 == 176
    assert working_days(date(2026, 8, 1), date(2026, 8, 31)) * 8 == 168


def test_a_fixed_holiday_at_a_weekend_moves_to_a_weekday() -> None:
    """4 July 2026 is a Saturday, so the Friday is the day off."""
    assert is_holiday(date(2026, 7, 3))
    assert not is_holiday(date(2026, 7, 4))


def test_juneteenth_only_counts_from_2021() -> None:
    assert is_holiday(date(2021, 6, 18))  # the 19th is a Saturday
    assert not any(day.month == 6 for day in federal_holidays(2019))


def test_thanksgiving_is_the_fourth_thursday() -> None:
    assert is_holiday(date(2025, 11, 27))
    assert is_holiday(date(2026, 11, 26))


@pytest.mark.parametrize("year", range(2024, 2031))
def test_every_year_has_its_eleven_holidays_on_weekdays(year: int) -> None:
    days = federal_holidays(year)
    assert len(days) == 11, sorted(days)
    assert all(day.weekday() < 5 for day in days), "a day off is never at a weekend"


def test_a_backwards_range_is_no_days() -> None:
    assert working_days(date(2025, 5, 31), date(2025, 5, 1)) == 0
