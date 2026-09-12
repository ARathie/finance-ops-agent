"""One email, two attachments, one invoice (docs/decisions.md #24).

A consultant working through their own firm sends the approved timesheet and
the firm's invoice for the same hours in one email. The timesheet proves the
hours and the approval; the invoice is where the printed total usually is.
"""

from datetime import date

import pytest

from finance_ops_agent.domain.checks import check_hours, combine_readings
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

HIGH = Confidence.HIGH
JULY = (date(2026, 7, 1), date(2026, 7, 31))


def make(
    *,
    consultant: str | None = "Ravi Balakrishnan",
    client: str | None = "Northwind Utilities",
    period: tuple[date, date] | None = JULY,
    rows: list[RowEntry] | None = None,
    stated: int | None = None,
    approval: ApprovalKind = ApprovalKind.NONE,
    month: date | None = None,
) -> TimesheetReading:
    start, end = period if period else (None, None)
    return TimesheetReading(
        consultant_name=ReadField[str](value=consultant, confidence=HIGH),
        client_name=ReadField[str](value=client, confidence=HIGH),
        end_client_name=ReadField[str](),
        period_start=ReadField[date](value=start, confidence=HIGH),
        period_end=ReadField[date](value=end, confidence=HIGH),
        stated_month_start=ReadField[date](value=month, confidence=HIGH),
        daily_entries=ReadField[list[DailyEntry]](value=[]),
        row_entries=ReadField[list[RowEntry]](value=rows or [], confidence=HIGH),
        stated_total_hours_hundredths=ReadField[int](value=stated, confidence=HIGH),
        approval=ReadField[Approval](value=Approval(kind=approval), confidence=HIGH),
    )


def week(first: date, hours: int) -> RowEntry:
    from datetime import timedelta

    return RowEntry(first_day=first, last_day=first + timedelta(days=6), hours_hundredths=hours)


JULY_ROWS = [
    week(date(2026, 6, 27), 3200),
    week(date(2026, 7, 4), 4000),
    week(date(2026, 7, 11), 4000),
    week(date(2026, 7, 18), 4000),
    week(date(2026, 7, 25), 4000),
]


def test_one_reading_is_returned_unchanged() -> None:
    only = make(approval=ApprovalKind.APPROVED_STATUS)
    combined, findings = combine_readings([only])
    assert combined is only
    assert findings == []


def test_no_readings_at_all_is_a_programming_error() -> None:
    with pytest.raises(ValueError):
        combine_readings([])


def test_the_invoices_total_settles_the_timesheets_part_week() -> None:
    """The whole point: apart, each document is incomplete; together they bill."""
    timesheet = make(rows=JULY_ROWS, approval=ApprovalKind.APPROVED_STATUS)
    invoice = make(stated=17600)

    # The timesheet alone cannot be billed: its 06/27 week runs into June.
    alone, _ = check_hours(timesheet)
    assert alone is None

    combined, findings = combine_readings([timesheet, invoice])
    total, hour_findings = check_hours(combined)
    assert total == 17600
    assert findings == []
    assert hour_findings == []


def test_the_order_of_the_attachments_does_not_matter() -> None:
    timesheet = make(rows=JULY_ROWS, approval=ApprovalKind.APPROVED_STATUS)
    invoice = make(stated=17600)
    first, _ = combine_readings([timesheet, invoice])
    second, _ = combine_readings([invoice, timesheet])
    assert check_hours(first)[0] == check_hours(second)[0] == 17600


def test_the_approval_comes_from_the_timesheet_not_the_invoice() -> None:
    """An invoice shows no approval; combining must not lose the timesheet's."""
    timesheet = make(approval=ApprovalKind.APPROVED_STATUS)
    invoice = make(stated=17600)
    combined, _ = combine_readings([invoice, timesheet])
    assert combined.approval.value is not None
    assert combined.approval.value.kind is ApprovalKind.APPROVED_STATUS


def test_attachments_that_disagree_about_the_consultant_go_to_kevin() -> None:
    timesheet = make(approval=ApprovalKind.APPROVED_STATUS)
    invoice = make(consultant="Someone Else", stated=17600)
    _, findings = combine_readings([timesheet, invoice])
    assert [f.code for f in findings] == [ReviewCode.NOT_SURE]
    assert "consultant" in findings[0].message


def test_attachments_are_expected_to_span_different_dates() -> None:
    """A weekly timesheet runs in whole weeks and a vendor invoice bills a
    calendar month, so their first and last dates rarely match. That is the
    normal shape of Icon's mail, not something to ask Kevin about: the span
    never decides the period (decision 26)."""
    timesheet = make(
        period=(date(2026, 6, 20), date(2026, 7, 31)), approval=ApprovalKind.APPROVED_STATUS
    )
    invoice = make(period=(date(2026, 7, 1), date(2026, 7, 31)), stated=17600)
    _, findings = combine_readings([timesheet, invoice])
    assert findings == []


def test_attachments_for_different_months_go_to_kevin() -> None:
    """Disagreeing about the month being billed is a real disagreement."""
    timesheet = make(month=date(2026, 7, 1), approval=ApprovalKind.APPROVED_STATUS)
    invoice = make(month=date(2026, 8, 1), stated=16800)
    _, findings = combine_readings([timesheet, invoice])
    assert [f.code for f in findings] == [ReviewCode.PERIOD_UNCLEAR]
    assert "July 2026" in findings[0].message and "August 2026" in findings[0].message


def test_a_blank_second_attachment_changes_nothing() -> None:
    """A logo or a signature image reads as nothing and must not pollute."""
    timesheet = make(rows=JULY_ROWS, stated=17600, approval=ApprovalKind.APPROVED_STATUS)
    nothing = make(consultant=None, client=None, period=None)
    combined, findings = combine_readings([timesheet, nothing])
    assert findings == []
    assert combined.consultant_name.value == "Ravi Balakrishnan"
    assert check_hours(combined)[0] == 17600
