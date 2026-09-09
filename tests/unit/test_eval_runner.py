from datetime import date
from pathlib import Path

from finance_ops_agent.application.eval_runner import (
    EvalCase,
    EvalReport,
    ExpectedCase,
    Thresholds,
    below_thresholds,
    run_eval,
    score_case,
)
from tests.scenarios.conftest import reading

AUG = (date(2026, 8, 1), date(2026, 8, 31))


def case(name: str = "case", codes: list[str] | None = None) -> EvalCase:
    return EvalCase(
        name=name,
        input_path=Path(f"/nowhere/{name}/input.csv"),
        expected=ExpectedCase(reading=reading(*AUG), review_codes=codes or []),
    )


def test_a_perfect_reading_scores_full_marks() -> None:
    report = run_eval([case()], lambda c: c.expected.reading)
    assert report.cases == 1
    assert report.field_accuracy_percent_floor("consultant_name") == 100
    assert report.mean_hours_error_hundredths_ceiling() == 0
    assert report.codes_accuracy_percent_floor() == 100
    assert report.failures == []


def test_wrong_hours_count_as_error_and_a_miss() -> None:
    report = EvalReport()
    score_case(report, case(), reading(*AUG, total_hundredths=15_000))
    assert report.field_correct.get("total_hours", 0) == 0
    assert report.hours_error_hundredths_total == 600
    assert any("total_hours" in failure for failure in report.failures)


def test_name_comparison_forgives_case_and_order() -> None:
    report = EvalReport()
    score_case(report, case(), reading(*AUG, consultant="SHAH, priya"))
    assert report.field_correct["consultant_name"] == 1


def test_missing_review_codes_are_a_miss() -> None:
    report = EvalReport()
    # The reading shows approval, but the case expected NO_APPROVAL to be raised.
    score_case(report, case(codes=["NO_APPROVAL"]), reading(*AUG))
    assert report.codes_exact == 0


def test_thresholds_catch_a_drop() -> None:
    report = EvalReport()
    score_case(report, case(), reading(*AUG, consultant="Somebody Else"))
    thresholds = Thresholds(
        field_accuracy_percent={"consultant_name": 90},
        mean_hours_error_hundredths_max=25,
        codes_accuracy_percent=85,
    )
    problems = below_thresholds(report, thresholds)
    assert any("consultant_name" in problem for problem in problems)
