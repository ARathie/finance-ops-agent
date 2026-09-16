"""US federal holidays, so a full-time month is measured against days actually
worked.

Icon's consultants are not expected to work public holidays (decision 28), so
the hours a full month should come to are eight for each weekday that is not a
holiday. Without this, the week of Memorial Day reads as 32 hours where 40
were expected, and a month containing two holidays looks 10 per cent light.

Pure arithmetic, no data file and no dependency: the eleven federal holidays
are each a fixed date or an nth weekday, and a fixed date falling at a weekend
is observed on the nearest weekday.
"""

from datetime import date, timedelta

MONDAY, THURSDAY, SATURDAY, SUNDAY = 0, 3, 5, 6


def _nth_weekday(year: int, month: int, weekday: int, nth: int) -> date:
    """The nth given weekday of a month; nth=-1 means the last one."""
    if nth < 0:
        day = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
        while day.weekday() != weekday:
            day -= timedelta(days=1)
        return day
    day = date(year, month, 1)
    while day.weekday() != weekday:
        day += timedelta(days=1)
    return day + timedelta(weeks=nth - 1)


def _observed(day: date) -> date:
    """A fixed-date holiday at a weekend is kept on the nearest weekday."""
    if day.weekday() == SATURDAY:
        return day - timedelta(days=1)
    if day.weekday() == SUNDAY:
        return day + timedelta(days=1)
    return day


def federal_holidays(year: int) -> set[date]:
    """The eleven US federal holidays for a year, as observed."""
    fixed = [date(year, 1, 1), date(year, 7, 4), date(year, 11, 11), date(year, 12, 25)]
    if year >= 2021:  # Juneteenth became federal in 2021
        fixed.append(date(year, 6, 19))
    floating = [
        _nth_weekday(year, 1, MONDAY, 3),  # Martin Luther King Jr. Day
        _nth_weekday(year, 2, MONDAY, 3),  # Washington's Birthday
        _nth_weekday(year, 5, MONDAY, -1),  # Memorial Day
        _nth_weekday(year, 9, MONDAY, 1),  # Labor Day
        _nth_weekday(year, 10, MONDAY, 2),  # Columbus Day
        _nth_weekday(year, 11, THURSDAY, 4),  # Thanksgiving
    ]
    return {_observed(day) for day in fixed} | set(floating)


def is_holiday(day: date) -> bool:
    return day in federal_holidays(day.year)


def working_days(start: date, end: date) -> int:
    """Weekdays between two dates, both included, that are not a holiday."""
    if end < start:
        return 0
    holidays: set[date] = set()
    for year in range(start.year, end.year + 1):
        holidays |= federal_holidays(year)
    count, day = 0, start
    while day <= end:
        if day.weekday() < SATURDAY and day not in holidays:
            count += 1
        day += timedelta(days=1)
    return count
