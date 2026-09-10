from datetime import date
from pathlib import Path

from finance_ops_agent.application.eval_runner import (
    MODEL_PRICES,
    EvalCase,
    EvalReport,
    ExpectedCase,
    ModelPrices,
    Thresholds,
    UsageReport,
    below_thresholds,
    cost_millicents,
    format_dollars,
    run_eval,
    score_case,
)
from finance_ops_agent.ports.reader import TokenUsage
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


def prices() -> ModelPrices:
    """Claude Opus 5's published list prices, in cents per million tokens."""
    return ModelPrices(
        input_cents_per_million=500,
        output_cents_per_million=2_500,
        cache_write_cents_per_million=625,
        cache_read_cents_per_million=50,
    )


def test_the_built_in_price_table_knows_the_model_the_reader_defaults_to() -> None:
    assert MODEL_PRICES["claude-opus-5"] == prices()


def test_cost_is_whole_thousandths_of_a_cent_with_no_floats() -> None:
    # A million input tokens at $5.00 is 500 cents is 500_000 thousandths.
    million_in = TokenUsage(requests=1, input_tokens=1_000_000)
    assert cost_millicents(million_in, prices()) == 500_000
    million_out = TokenUsage(requests=1, output_tokens=1_000_000)
    assert cost_millicents(million_out, prices()) == 2_500_000


def test_cached_input_is_charged_at_its_own_two_rates() -> None:
    """Writing the prompt into the cache costs more than reading it back."""
    written = TokenUsage(requests=1, cache_creation_input_tokens=1_000_000)
    read_back = TokenUsage(requests=1, cache_read_input_tokens=1_000_000)
    assert cost_millicents(written, prices()) == 625_000  # 1.25x input
    assert cost_millicents(read_back, prices()) == 50_000  # 0.1x input
    assert cost_millicents(written, prices()) > cost_millicents(read_back, prices())


def test_usage_adds_up_across_calls() -> None:
    one = TokenUsage(requests=1, input_tokens=100, output_tokens=20)
    two = TokenUsage(requests=1, input_tokens=300, cache_read_input_tokens=50)
    total = one + two
    assert total.requests == 2
    assert total.input_tokens == 400
    assert total.output_tokens == 20
    assert total.cache_read_input_tokens == 50
    assert total.total_input_tokens == 450  # cached input counts as input read


def test_the_report_gives_the_cost_per_timesheet() -> None:
    usage = TokenUsage(requests=4, input_tokens=400_000, output_tokens=40_000)
    report = UsageReport(usage=usage, cases=4, prices=prices(), model="claude-opus-5")
    # 400_000 in at $5/M is $2.00; 40_000 out at $25/M is $1.00.
    assert report.total_millicents() == 300_000
    assert "$3.0000 in total" in report.format()
    assert "$0.7500 per timesheet" in report.format()
    assert "per timesheet: 100000 in, 10000 out" in report.format()


def test_a_report_with_no_price_for_the_model_says_so_rather_than_guessing() -> None:
    report = UsageReport(
        usage=TokenUsage(requests=1, input_tokens=10), cases=1, prices=None, model="some-new-model"
    )
    assert report.total_millicents() is None
    assert "no price recorded for some-new-model" in report.format()


def test_an_empty_run_does_not_divide_by_zero() -> None:
    report = UsageReport(usage=TokenUsage(), cases=0, prices=prices(), model="claude-opus-5")
    assert report.per_case(1_000) == 0
    assert report.format()


def test_dollars_are_formatted_from_integers() -> None:
    assert format_dollars(300_000) == "$3.0000"
    assert format_dollars(4_250) == "$0.0425"
    assert format_dollars(0) == "$0.0000"
