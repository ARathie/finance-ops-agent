"""Billing periods for each schedule, clipped to the engagement's dates.

The schedules and their period boundaries are defined in docs/engagement-list.md:
monthly = calendar month; twice a month = 1st-15th and 16th-month end;
every two weeks = 14-day periods counted from "First period start";
weekly = 7-day periods counted from "First period start". The first and last
period of an engagement are cut short at its start and end dates.
"""

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum


class BillingSchedule(StrEnum):
    """Spelled exactly as Kevin writes them in the engagement list."""

    MONTHLY = "monthly"
    TWICE_A_MONTH = "twice a month"
    EVERY_TWO_WEEKS = "every two weeks"
    WEEKLY = "weekly"


_ANCHORED_LENGTH_DAYS = {
    BillingSchedule.EVERY_TWO_WEEKS: 14,
    BillingSchedule.WEEKLY: 7,
}


@dataclass(frozen=True)
class BillingPeriod:
    start: date
    end: date  # inclusive

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise ValueError(f"period starts {self.start} after it ends {self.end}")

    def covers(self, day: date) -> bool:
        return self.start <= day <= self.end


def _month_end(day: date) -> date:
    return day.replace(day=calendar.monthrange(day.year, day.month)[1])


def _raw_period(
    schedule: BillingSchedule, day: date, first_period_start: date | None
) -> tuple[date, date]:
    """The full period containing `day`, before clipping to the engagement."""
    if schedule is BillingSchedule.MONTHLY:
        return day.replace(day=1), _month_end(day)
    if schedule is BillingSchedule.TWICE_A_MONTH:
        if day.day <= 15:
            return day.replace(day=1), day.replace(day=15)
        return day.replace(day=16), _month_end(day)
    if first_period_start is None:
        raise ValueError(f"schedule {schedule!r} needs a first period start")
    length = _ANCHORED_LENGTH_DAYS[schedule]
    offset = (day - first_period_start).days % length
    start = day - timedelta(days=offset)
    return start, start + timedelta(days=length - 1)


def period_containing(
    day: date,
    schedule: BillingSchedule,
    engagement_start: date,
    engagement_end: date | None = None,
    first_period_start: date | None = None,
) -> BillingPeriod:
    """The billing period covering `day`, cut short at the engagement's start and end."""
    if day < engagement_start:
        raise ValueError(f"{day} is before the engagement starts {engagement_start}")
    if engagement_end is not None and day > engagement_end:
        raise ValueError(f"{day} is after the engagement ends {engagement_end}")
    raw_start, raw_end = _raw_period(schedule, day, first_period_start)
    start = max(raw_start, engagement_start)
    end = raw_end if engagement_end is None else min(raw_end, engagement_end)
    return BillingPeriod(start, end)


def billing_periods(
    schedule: BillingSchedule,
    engagement_start: date,
    engagement_end: date | None = None,
    first_period_start: date | None = None,
    *,
    until: date,
) -> list[BillingPeriod]:
    """Every billing period of the engagement that starts on or before `until`, in order.

    The last period returned may end after `until` (a period still in progress);
    callers that want only finished periods filter on `period.end`.
    """
    effective_end = until if engagement_end is None else min(engagement_end, until)
    periods: list[BillingPeriod] = []
    day = engagement_start
    while day <= effective_end:
        period = period_containing(
            day, schedule, engagement_start, engagement_end, first_period_start
        )
        periods.append(period)
        day = period.end + timedelta(days=1)
    return periods
