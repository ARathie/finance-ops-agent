"""Which month a weekly timesheet is for, and whose hours belong to it.

Decision 26. Icon's timesheets are exports listing one row per week, so their
rows run past both ends of the month being billed and can never sit inside one
billing period. The numbers here are the two real Sridhar months: a July
invoice totalling 176 hours whose 06/27 week contributes 16 of its 32, and an
August one totalling 168 whose 08/29 week contributes 8 of its 40. Both
timesheets also print a neighbouring month's week for context, which must not
be billed.
"""

from datetime import date, timedelta

import pytest

from finance_ops_agent.domain.checks import check_hours, fit_billing_period
from finance_ops_agent.domain.engagements import Engagement
from finance_ops_agent.domain.money import Money
from finance_ops_agent.domain.periods import BillingPeriod, BillingSchedule
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


def week(start: date, hours: int) -> RowEntry:
    """One row, dated by the day the week starts (the column Icon's system prints)."""
    return RowEntry(
        label=start.strftime("%-m/%d/%Y"),
        first_day=start,
        last_day=start + timedelta(days=6),
        hours_hundredths=hours * 100,
    )


JULY_ROWS = [
    week(date(2026, 6, 20), 40),  # wholly June: printed for context, never billed
    week(date(2026, 6, 27), 32),  # straddles; the note says 16 belong to July
    week(date(2026, 7, 4), 40),
    week(date(2026, 7, 11), 40),
    week(date(2026, 7, 18), 40),
    week(date(2026, 7, 25), 40),
]
AUGUST_ROWS = [
    week(date(2026, 7, 25), 40),  # wholly July: already billed on July's invoice
    week(date(2026, 8, 1), 40),
    week(date(2026, 8, 8), 40),
    week(date(2026, 8, 15), 40),
    week(date(2026, 8, 22), 40),
    week(date(2026, 8, 29), 40),  # straddles; the note says 8 belong to August
]


def reading(
    rows: list[RowEntry],
    stated: int | None = None,
    month: date | None = None,
    noted: int | None = None,
) -> TimesheetReading:
    return TimesheetReading(
        consultant_name=ReadField[str](value="Sridhar Doraiswamy", confidence=HIGH),
        client_name=ReadField[str](value="MasTec", confidence=HIGH),
        end_client_name=ReadField[str](),
        period_start=ReadField[date](value=rows[0].first_day, confidence=HIGH),
        period_end=ReadField[date](value=rows[-1].last_day, confidence=HIGH),
        stated_month_start=ReadField[date](value=month, confidence=HIGH),
        noted_in_month_hundredths=ReadField[int](value=noted, confidence=HIGH),
        daily_entries=ReadField[list[DailyEntry]](value=[]),
        row_entries=ReadField[list[RowEntry]](value=rows, confidence=HIGH),
        stated_total_hours_hundredths=ReadField[int](value=stated, confidence=HIGH),
        approval=ReadField[Approval](
            value=Approval(kind=ApprovalKind.APPROVED_STATUS, approver="Ajaya Rautray"),
            confidence=HIGH,
        ),
    )


def engagement() -> Engagement:
    return Engagement(
        consultant="Sridhar Doraiswamy",
        client="MasTec",
        end_client="",
        role="Consultant",
        start_date=date(2026, 7, 1),
        end_date=date(2026, 8, 31),
        billing_schedule=BillingSchedule.MONTHLY,
        first_period_start=None,
        bill_rate=Money(13_500),
        pay_rate=Money(11_000),
        rates_from=date(2026, 7, 1),
        send_automatically=False,
        active=True,
        row_number=2,
    )


class TestWhichMonth:
    def test_a_month_named_on_the_invoice_settles_it(self) -> None:
        got, findings = fit_billing_period(engagement(), reading(JULY_ROWS, month=date(2026, 7, 1)))
        assert got == BillingPeriod(date(2026, 7, 1), date(2026, 7, 31))
        assert findings == []

    def test_without_a_stated_month_the_bulk_of_the_days_decides(self) -> None:
        """August's sheet runs 07/25 to 09/04; August holds most of it."""
        got, findings = fit_billing_period(engagement(), reading(AUGUST_ROWS))
        assert got == BillingPeriod(date(2026, 8, 1), date(2026, 8, 31))
        assert findings == []

    def test_the_weeks_may_overhang_the_month_at_both_ends(self) -> None:
        """The old rule required the span to sit inside one period, which a
        weekly timesheet never does; that is what decision 26 removed."""
        sheet = reading(JULY_ROWS, month=date(2026, 7, 1))
        assert sheet.period_start.value == date(2026, 6, 20)
        assert sheet.period_end.value == date(2026, 7, 31)
        got, findings = fit_billing_period(engagement(), sheet)
        assert got is not None and findings == []

    def test_dates_that_say_nothing_are_still_a_review(self) -> None:
        blank = reading(JULY_ROWS).model_copy(
            update={"period_start": ReadField[date](), "period_end": ReadField[date]()}
        )
        got, findings = fit_billing_period(engagement(), blank)
        assert got is None
        assert [f.code for f in findings] == [ReviewCode.PERIOD_UNCLEAR]

    def test_a_month_outside_the_engagement_is_a_mismatch(self) -> None:
        got, findings = fit_billing_period(engagement(), reading(JULY_ROWS, month=date(2025, 7, 1)))
        assert got is None
        assert [f.code for f in findings] == [ReviewCode.PERIOD_MISMATCH]


class TestHoursForTheMonth:
    JULY = BillingPeriod(date(2026, 7, 1), date(2026, 7, 31))
    AUGUST = BillingPeriod(date(2026, 8, 1), date(2026, 8, 31))

    def test_july_comes_to_the_invoiced_176(self) -> None:
        total, findings = check_hours(
            reading(JULY_ROWS, stated=17_600, month=date(2026, 7, 1), noted=1_600),
            self.JULY,
        )
        assert total == 17_600
        assert findings == []

    def test_august_comes_to_the_invoiced_168(self) -> None:
        total, findings = check_hours(
            reading(AUGUST_ROWS, stated=16_800, month=date(2026, 8, 1), noted=800),
            self.AUGUST,
        )
        assert total == 16_800
        assert findings == []

    def test_another_months_week_printed_for_context_is_never_billed(self) -> None:
        """June's 06/20 week is on July's sheet. Summing it would bill 40 hours
        that belong to June's invoice."""
        total, _ = check_hours(reading(JULY_ROWS, month=date(2026, 7, 1), noted=1_600), self.JULY)
        assert total == 17_600  # 4 whole July weeks + the note's 16, not 216

    def test_a_note_disagreeing_with_the_invoice_asks_kevin(self) -> None:
        total, findings = check_hours(
            reading(JULY_ROWS, stated=17_600, month=date(2026, 7, 1), noted=2_400),
            self.JULY,
        )
        assert [f.code for f in findings] == [ReviewCode.PART_WEEK_DISAGREES]
        assert total == 17_600  # the printed total still leads (decision 24)

    def test_a_straddling_week_with_no_note_and_no_total_still_asks(self) -> None:
        total, findings = check_hours(reading(JULY_ROWS), self.JULY)
        assert total is None
        assert [f.code for f in findings] == [ReviewCode.PART_WEEK_UNCLEAR]

    def test_the_money_the_july_invoice_comes_to(self) -> None:
        total, _ = check_hours(
            reading(JULY_ROWS, stated=17_600, month=date(2026, 7, 1), noted=1_600),
            self.JULY,
        )
        assert total is not None
        assert Money(engagement().bill_rate.cents * total // 100) == Money(2_376_000)


@pytest.mark.parametrize("period", [None])
def test_without_a_period_the_old_behaviour_is_unchanged(period: BillingPeriod | None) -> None:
    """Callers that have no billing period yet still judge rows against the
    timesheet's own span, as they did before decision 26."""
    total, findings = check_hours(reading(JULY_ROWS, stated=17_600), period)
    assert total == 17_600
    # And it shows why the period matters: with none, June's context week is
    # counted among the rows and the total looks wrong.
    assert [f.code for f in findings] == [ReviewCode.HOURS_DONT_ADD_UP]


class TestDailyTimesheetsSpanningMonths:
    """A daily timesheet's weeks run Sunday to Saturday, so a month's pages
    carry days from the months either side (decision 27). The real May 2025
    export runs from Sunday 27 April: 192 hours across the document, of which
    168 are May. Summing the lot billed 24 hours that belong to April, and
    nothing flagged it -- 192 is only 9% over full time for May, well inside
    the "unusual" threshold.
    """

    MAY = BillingPeriod(date(2025, 5, 1), date(2025, 5, 31))

    def days(self) -> list[DailyEntry]:
        worked = [
            date(2025, 4, 28),
            date(2025, 4, 29),
            date(2025, 4, 30),
            date(2025, 5, 1),
            date(2025, 5, 2),
        ]
        for start in (date(2025, 5, 5), date(2025, 5, 12), date(2025, 5, 19)):
            worked += [start + timedelta(days=i) for i in range(5)]
        worked += [date(2025, 5, 26), date(2025, 5, 27), date(2025, 5, 28), date(2025, 5, 29)]
        return [DailyEntry(day=day, hours_hundredths=800) for day in worked]

    def sheet(self, stated: int | None = None) -> TimesheetReading:
        return TimesheetReading(
            consultant_name=ReadField[str](value="Subramanian Arumugam", confidence=HIGH),
            client_name=ReadField[str](value="iStream", confidence=HIGH),
            end_client_name=ReadField[str](),
            period_start=ReadField[date](value=date(2025, 4, 27), confidence=HIGH),
            period_end=ReadField[date](value=date(2025, 5, 31), confidence=HIGH),
            daily_entries=ReadField[list[DailyEntry]](value=self.days(), confidence=HIGH),
            stated_total_hours_hundredths=ReadField[int](value=stated, confidence=HIGH),
            approval=ReadField[Approval](
                value=Approval(kind=ApprovalKind.APPROVED_STATUS, approver="Ramana Akula"),
                confidence=HIGH,
            ),
        )

    def test_only_the_days_in_the_month_are_billed(self) -> None:
        total, findings = check_hours(self.sheet(), self.MAY)
        assert total == 16_800  # not the 19_200 on the document
        assert findings == []

    def test_the_document_really_does_hold_192_hours(self) -> None:
        """The bug it replaces: every day summed, April included."""
        assert sum(day.hours_hundredths for day in self.days()) == 19_200

    def test_a_total_covering_the_whole_document_does_not_win(self) -> None:
        """192 is printed across five weekly pages; only 168 are May, and the
        dated days settle it rather than the total."""
        total, findings = check_hours(self.sheet(stated=19_200), self.MAY)
        assert total == 16_800
        assert [f.code for f in findings] == [ReviewCode.HOURS_DONT_ADD_UP]

    def test_a_total_that_agrees_is_left_alone(self) -> None:
        total, findings = check_hours(self.sheet(stated=16_800), self.MAY)
        assert total == 16_800
        assert findings == []
