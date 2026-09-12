from datetime import date

from finance_ops_agent.domain import checks
from finance_ops_agent.domain.engagements import parse_workbook
from finance_ops_agent.domain.periods import BillingPeriod
from finance_ops_agent.domain.reading import Confidence
from finance_ops_agent.domain.review import ReviewCode
from tests.scenarios.conftest import (
    default_workbook,
    engagement_row,
    reading,
)

AUG = BillingPeriod(date(2026, 8, 1), date(2026, 8, 31))


class TestNamesMatch:
    def test_ignores_case_punctuation_and_order(self) -> None:
        assert checks.names_match("Priya Shah", "priya shah")
        assert checks.names_match("Shah, Priya", "Priya Shah")
        assert checks.names_match("P. Shah", "p shah")
        assert not checks.names_match("Priya Shah", "Dana Cruz")
        assert not checks.names_match("", "")


class TestMatchConsultant:
    def test_sender_address_wins(self) -> None:
        parsed = parse_workbook(default_workbook())
        consultant, findings = checks.match_consultant(
            "priya@example.com", reading(AUG.start, AUG.end), parsed.consultants
        )
        assert consultant is not None and consultant.name == "Priya Shah"
        assert findings == []

    def test_name_on_the_timesheet_is_the_fallback(self) -> None:
        parsed = parse_workbook(default_workbook())
        consultant, findings = checks.match_consultant(
            "somebody@elsewhere.example",
            reading(AUG.start, AUG.end, consultant="Shah, Priya"),
            parsed.consultants,
        )
        assert consultant is not None and consultant.name == "Priya Shah"

    def test_no_match_is_a_review(self) -> None:
        parsed = parse_workbook(default_workbook())
        consultant, findings = checks.match_consultant(
            "somebody@elsewhere.example",
            reading(AUG.start, AUG.end, consultant="Nobody Known"),
            parsed.consultants,
        )
        assert consultant is None
        assert findings[0].code is ReviewCode.CONSULTANT_UNKNOWN


class TestRateRow:
    def test_latest_row_on_or_before_the_period_start(self) -> None:
        parsed = parse_workbook(default_workbook())
        rows = [row for row in parsed.engagements if row.consultant == "Priya Shah"]
        row, findings = checks.rate_row_in_force(rows, AUG)
        assert row is not None and findings == []

    def test_no_rate_for_these_dates(self) -> None:
        parsed = parse_workbook(default_workbook())
        rows = [row for row in parsed.engagements if row.consultant == "Priya Shah"]
        july = BillingPeriod(date(2026, 7, 1), date(2026, 7, 31))
        row, findings = checks.rate_row_in_force(rows, july)
        assert row is None
        assert findings[0].code is ReviewCode.RATE_MISSING

    def test_rate_change_mid_period_is_a_review(self) -> None:
        raw = default_workbook()
        raw.engagements.append(
            engagement_row(9, **{"Rates from": "2026-08-15", "Bill rate": "150.00"})
        )
        parsed = parse_workbook(raw)
        rows = [row for row in parsed.engagements if row.consultant == "Priya Shah"]
        row, findings = checks.rate_row_in_force(rows, AUG)
        assert row is None
        assert findings[0].code is ReviewCode.LIST_ROW_PROBLEM
        assert "row 9" in findings[0].message


class TestHours:
    def test_missing(self) -> None:
        total, findings = checks.check_hours(reading(AUG.start, AUG.end, total_hundredths=None))
        assert total is None
        assert findings[0].code is ReviewCode.HOURS_MISSING

    def test_daily_hours_within_a_quarter_hour_are_fine(self) -> None:
        dailies = [(date(2026, 8, day), 779) for day in range(3, 13)]  # sums to 77.90
        total, findings = checks.check_hours(
            reading(date(2026, 8, 1), date(2026, 8, 14), 7_800, dailies=dailies)
        )
        assert findings == []
        assert total == 7_800  # the printed total is what gets invoiced

    def test_daily_hours_off_by_more_are_a_review(self) -> None:
        dailies = [(date(2026, 8, day), 700) for day in range(3, 13)]  # sums to 70.00
        _, findings = checks.check_hours(
            reading(date(2026, 8, 1), date(2026, 8, 14), 7_800, dailies=dailies)
        )
        assert [finding.code for finding in findings] == [ReviewCode.HOURS_DONT_ADD_UP]

    def test_zero_hours_are_unusual(self) -> None:
        _, findings = checks.check_hours(reading(AUG.start, AUG.end, 0))
        assert [finding.code for finding in findings] == [ReviewCode.HOURS_UNUSUAL]

    def test_more_than_24_hours_in_a_day_is_unusual(self) -> None:
        dailies = [(date(2026, 8, 3), 2_500)]
        _, findings = checks.check_hours(
            reading(date(2026, 8, 3), date(2026, 8, 3), 2_500, dailies=dailies)
        )
        assert ReviewCode.HOURS_UNUSUAL in [finding.code for finding in findings]

    def test_over_full_time_is_unusual(self) -> None:
        # August 2026 has 21 weekdays; full time 168 h; 5% over is 176.4 h.
        _, ok = checks.check_hours(reading(AUG.start, AUG.end, 17_600))
        _, over = checks.check_hours(reading(AUG.start, AUG.end, 17_700))
        assert ok == []
        assert [finding.code for finding in over] == [ReviewCode.HOURS_UNUSUAL]


class TestApprovalAndConfidence:
    def test_no_approval(self) -> None:
        findings = checks.check_approval(reading(AUG.start, AUG.end, approved=False))
        assert [finding.code for finding in findings] == [ReviewCode.NO_APPROVAL]

    def test_approved(self) -> None:
        assert checks.check_approval(reading(AUG.start, AUG.end)) == []

    def test_low_confidence_is_a_review(self) -> None:
        findings = checks.check_confidence(reading(AUG.start, AUG.end, confidence=Confidence.LOW))
        assert [finding.code for finding in findings] == [ReviewCode.NOT_SURE]

    def test_medium_confidence_passes_the_check(self) -> None:
        assert (
            checks.check_confidence(reading(AUG.start, AUG.end, confidence=Confidence.MEDIUM)) == []
        )


class TestCoverage:
    def test_full_period_covered(self) -> None:
        spans = [(date(2026, 8, 1), date(2026, 8, 15)), (date(2026, 8, 16), date(2026, 8, 31))]
        assert checks.period_fully_covered(AUG, spans)

    def test_a_gap_means_still_waiting(self) -> None:
        spans = [(date(2026, 8, 1), date(2026, 8, 15)), (date(2026, 8, 20), date(2026, 8, 31))]
        assert not checks.period_fully_covered(AUG, spans)
