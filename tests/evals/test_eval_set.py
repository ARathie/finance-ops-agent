"""The committed test set: 40+ made-up timesheets, replayed from recorded
responses only (no network in CI), scored against the recorded thresholds.
"""

import json
from pathlib import Path

from finance_ops_agent.application.eval_runner import (
    EvalCase,
    Thresholds,
    below_thresholds,
    load_cases,
    run_eval,
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
