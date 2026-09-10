"""Weekly timesheets, and the week that straddles the month end.

Icon's timesheets list hours per week, not per day, and the week at each end
of a month covers days on both sides of it (docs/decisions.md #24). The
numbers here are the ones on the sample documents: a July invoice totalling
176 hours, whose 06/27 week contributes 16 of its 32 logged hours, and an
August one totalling 168, whose 08/29 week contributes 8 of its 40.
"""

from datetime import date

from finance_ops_agent.domain.checks import check_hours
from finance_ops_agent.domain.reading import (
    Approval,
    ApprovalKind,
    Confidence,
    DailyEntry,
    ReadField,
    RowEntry,
    TimesheetReading,
)
from finance_ops_agent.domain.review import ReviewCode


def reading(
    rows: list[RowEntry],
    stated: int | None,
    start: date = date(2026, 7, 1),
    end: date = date(2026, 7, 31),
) -> TimesheetReading:
    high = Confidence.HIGH
    return TimesheetReading(
        consultant_name=ReadField[str](value="Sridhar Doraiswamy", confidence=high),
        client_name=ReadField[str](value="MasTec", confidence=high),
        end_client_name=ReadField[str](),
        period_start=ReadField[date](value=start, confidence=high),
        period_end=ReadField[date](value=end, confidence=high),
        daily_entries=ReadField[list[DailyEntry]](value=[]),
        row_entries=ReadField[list[RowEntry]](value=rows, confidence=high),
        stated_total_hours_hundredths=ReadField[int](value=stated, confidence=high),
        approval=ReadField[Approval](
            value=Approval(kind=ApprovalKind.APPROVED_STATUS, approver="Ajaya Rautray"),
            confidence=high,
        ),
    )


def week(first: date, hours: int) -> RowEntry:
    from datetime import timedelta

    return RowEntry(
        label=first.strftime("%m/%d/%Y"),
        first_day=first,
        last_day=first + timedelta(days=6),
        hours_hundredths=hours,
    )


JULY_WEEKS_AS_LOGGED = [
    week(date(2026, 6, 27), 3200),  # 32 logged, only 16 fall in July
    week(date(2026, 7, 4), 4000),
    week(date(2026, 7, 11), 4000),
    week(date(2026, 7, 18), 4000),
    week(date(2026, 7, 25), 4000),
]


def test_a_week_running_past_the_period_is_not_summed() -> None:
    """Summing the rows would bill 192 hours where the invoice says 176."""
    total, findings = check_hours(reading(JULY_WEEKS_AS_LOGGED, stated=None))
    assert total is None
    assert [f.code for f in findings] == [ReviewCode.PART_WEEK_UNCLEAR]


def test_a_printed_total_settles_the_part_week() -> None:
    """The vendor invoice prints "176 hours", so nothing has to be guessed."""
    total, findings = check_hours(reading(JULY_WEEKS_AS_LOGGED, stated=17600))
    assert total == 17600
    assert [f.code for f in findings] == []


def test_weeks_inside_the_period_are_summed_without_a_printed_total() -> None:
    rows = [
        week(date(2026, 7, 4), 4000),
        week(date(2026, 7, 11), 4000),
        week(date(2026, 7, 18), 4000),
    ]
    total, findings = check_hours(
        reading(rows, stated=None, start=date(2026, 7, 4), end=date(2026, 7, 24))
    )
    assert total == 12000
    assert [f.code for f in findings] == []


def test_weekly_rows_that_contradict_the_printed_total_are_a_review() -> None:
    rows = [week(date(2026, 7, 4), 4000), week(date(2026, 7, 11), 4000)]
    _, findings = check_hours(
        reading(rows, stated=16000, start=date(2026, 7, 4), end=date(2026, 7, 17))
    )
    assert ReviewCode.HOURS_DONT_ADD_UP in [f.code for f in findings]


def test_august_matches_the_invoice() -> None:
    """168 hours: four full weeks plus 8 hours of the week starting 08/29."""
    rows = [
        week(date(2026, 8, 1), 4000),
        week(date(2026, 8, 8), 4000),
        week(date(2026, 8, 15), 4000),
        week(date(2026, 8, 22), 4000),
        week(date(2026, 8, 29), 4000),  # 40 logged, only 8 fall in August
    ]
    august = {"start": date(2026, 8, 1), "end": date(2026, 8, 31)}
    total, findings = check_hours(reading(rows, stated=None, **august))
    assert total is None
    assert [f.code for f in findings] == [ReviewCode.PART_WEEK_UNCLEAR]

    total, findings = check_hours(reading(rows, stated=16800, **august))
    assert total == 16800
    assert [f.code for f in findings] == []


def test_a_timesheet_with_no_rows_at_all_is_still_hours_missing() -> None:
    total, findings = check_hours(reading([], stated=None))
    assert total is None
    assert [f.code for f in findings] == [ReviewCode.HOURS_MISSING]


def test_a_well_read_weekly_timesheet_is_not_called_unsure() -> None:
    """The hours are in the weekly rows, so the empty daily field says nothing.

    Without this, every weekly timesheet without a printed total raised a
    spurious NOT_SURE and reached Kevin as a review for no reason.
    """
    from finance_ops_agent.domain.checks import check_confidence

    inside = [
        week(date(2026, 7, 4), 4000),
        week(date(2026, 7, 11), 4000),
    ]
    confident = reading(inside, stated=None, start=date(2026, 7, 4), end=date(2026, 7, 17))
    assert check_confidence(confident) == []


def test_weekly_rows_read_badly_are_still_a_review() -> None:
    from finance_ops_agent.domain.checks import check_confidence

    unsure = reading(
        [week(date(2026, 7, 4), 4000)], stated=None, start=date(2026, 7, 4), end=date(2026, 7, 10)
    )
    unsure = unsure.model_copy(
        update={"row_entries": unsure.row_entries.model_copy(update={"confidence": Confidence.LOW})}
    )
    assert [f.code for f in check_confidence(unsure)] == [ReviewCode.NOT_SURE]
