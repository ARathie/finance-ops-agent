"""The committed test set: 40+ made-up timesheets, replayed from recorded
responses only (no network in CI), scored against the recorded thresholds.
"""

import json
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from finance_ops_agent.adapters.claude.reader import TIMESHEET_PROMPT_VERSION
from finance_ops_agent.application.eval_runner import (
    EvalCase,
    Thresholds,
    below_thresholds,
    load_cases,
    run_eval,
    stamp_live_provenance,
)
from finance_ops_agent.domain.reading import TimesheetReading

CASES_DIR = Path(__file__).parent / "timesheets"
THRESHOLDS = Path(__file__).parent / "thresholds.json"


def recorded_reading(case: EvalCase) -> TimesheetReading:
    recorded = case.input_path.parent / "recorded.json"
    return TimesheetReading.model_validate(json.loads(recorded.read_text()))


def test_the_set_has_at_least_forty_cases() -> None:
    assert len(load_cases(CASES_DIR)) >= 40


def test_every_case_has_a_recorded_response() -> None:
    for case in load_cases(CASES_DIR):
        assert (case.input_path.parent / "recorded.json").exists(), case.name


def test_the_set_spans_the_documented_formats_and_conditions() -> None:
    cases = load_cases(CASES_DIR)
    suffixes = {case.input_path.suffix for case in cases}
    assert {".csv", ".xlsx", ".pdf", ".png", ".docx"} <= suffixes
    all_codes = {code for case in cases for code in case.expected.review_codes}
    assert {
        "NO_APPROVAL",
        "HOURS_DONT_ADD_UP",
        "HOURS_UNUSUAL",
        "HOURS_MISSING",
        "NOT_SURE",
    } <= all_codes


def test_recorded_responses_meet_the_recorded_thresholds() -> None:
    report = run_eval(load_cases(CASES_DIR), recorded_reading)
    thresholds = Thresholds.model_validate(json.loads(THRESHOLDS.read_text()))
    assert below_thresholds(report, thresholds) == [], report.format()


def loaded_thresholds() -> Thresholds:
    return Thresholds.model_validate(json.loads(THRESHOLDS.read_text()))


def test_the_thresholds_say_where_the_recorded_answers_came_from() -> None:
    """`source` is the record of whether these scores mean anything yet."""
    assert loaded_thresholds().source in {"bootstrap", "live"}


def test_live_thresholds_must_name_the_model_the_prompt_and_the_date() -> None:
    thresholds = loaded_thresholds()
    if thresholds.source == "live":
        assert thresholds.model is not None
        assert thresholds.prompt_version == TIMESHEET_PROMPT_VERSION, (
            "the recorded answers were written by a different prompt; re-run `fops eval --live`"
        )
        assert thresholds.recorded_on is not None


def test_calling_thresholds_live_without_the_provenance_is_refused() -> None:
    with pytest.raises(ValidationError):
        Thresholds(
            field_accuracy_percent={"total_hours": 90},
            mean_hours_error_hundredths_max=25,
            codes_accuracy_percent=85,
            source="live",
        )


def test_stamping_a_live_run_keeps_the_numbers_and_records_the_provenance() -> None:
    before = loaded_thresholds()
    after = stamp_live_provenance(
        before,
        model="claude-opus-5",
        prompt_version=TIMESHEET_PROMPT_VERSION,
        recorded_on=date(2026, 9, 10),
    )
    assert after.source == "live"
    assert after.model == "claude-opus-5"
    assert after.prompt_version == TIMESHEET_PROMPT_VERSION
    assert after.recorded_on == date(2026, 9, 10)
    assert after.field_accuracy_percent == before.field_accuracy_percent
    assert after.mean_hours_error_hundredths_max == before.mean_hours_error_hundredths_max
    assert after.codes_accuracy_percent == before.codes_accuracy_percent
    assert not after.proves_the_harness_only()


def test_bootstrap_recorded_answers_are_copies_of_the_expected_ones() -> None:
    """While `source` is `bootstrap`, that is exactly what the files are.

    This is what makes the reported scores meaningless, and it is why PR 12
    exists. When `fops eval --live` replaces them the copies stop matching and
    this test says so, so nobody can leave `source` at `bootstrap` by accident.
    """
    thresholds = loaded_thresholds()
    copies = [
        case.name
        for case in load_cases(CASES_DIR)
        if recorded_reading(case) == case.expected.reading
    ]
    if thresholds.source == "bootstrap":
        assert copies, 'no recorded answer is a bootstrap copy; set "source" to "live"'
    else:
        assert not copies, f"still bootstrap copies after a live run: {copies}"
