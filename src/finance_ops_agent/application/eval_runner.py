"""Score the timesheet reader against the made-up test set.

Each case folder holds the input file, `expected.json` (the correct reading and
the review codes the checks should raise), and `recorded.json` (the reader's
saved answer, which CI replays so no network is ever needed). The runner
compares readings field by field, sums the hours error, and re-runs the
document-only checks (hours, approval, confidence) on each answer to compare
review codes. All arithmetic is integer; the report only formats.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from finance_ops_agent.domain import checks
from finance_ops_agent.domain.reading import ApprovalKind, TimesheetReading

SCORED_FIELDS = (
    "consultant_name",
    "client_name",
    "period_start",
    "period_end",
    "total_hours",
    "approval_kind",
)


class ExpectedCase(BaseModel):
    model_config = ConfigDict(frozen=True)

    reading: TimesheetReading
    review_codes: list[str]


@dataclass(frozen=True)
class EvalCase:
    name: str
    input_path: Path
    expected: ExpectedCase


@dataclass
class EvalReport:
    cases: int = 0
    field_correct: dict[str, int] = field(default_factory=dict)
    hours_cases: int = 0
    hours_error_hundredths_total: int = 0
    codes_exact: int = 0
    failures: list[str] = field(default_factory=list)

    def field_accuracy_percent_floor(self, field_name: str) -> int:
        """Accuracy as a whole percent, rounded down. Integer arithmetic only."""
        if self.cases == 0:
            return 0
        return self.field_correct.get(field_name, 0) * 100 // self.cases

    def mean_hours_error_hundredths_ceiling(self) -> int:
        if self.hours_cases == 0:
            return 0
        return -(-self.hours_error_hundredths_total // self.hours_cases)

    def codes_accuracy_percent_floor(self) -> int:
        if self.cases == 0:
            return 0
        return self.codes_exact * 100 // self.cases

    def format(self) -> str:
        lines = [f"{self.cases} case(s):"]
        for field_name in SCORED_FIELDS:
            lines.append(
                f"  {field_name}: {self.field_correct.get(field_name, 0)}/{self.cases}"
                f" ({self.field_accuracy_percent_floor(field_name)}%)"
            )
        error = self.mean_hours_error_hundredths_ceiling()
        lines.append(f"  mean hours error: {error // 100}.{error % 100:02d} hours")
        lines.append(
            f"  review codes exactly right: {self.codes_exact}/{self.cases}"
            f" ({self.codes_accuracy_percent_floor()}%)"
        )
        for failure in self.failures:
            lines.append(f"  miss: {failure}")
        return "\n".join(lines)


def load_cases(cases_dir: Path) -> list[EvalCase]:
    cases: list[EvalCase] = []
    for case_dir in sorted(path for path in cases_dir.iterdir() if path.is_dir()):
        expected = ExpectedCase.model_validate(json.loads((case_dir / "expected.json").read_text()))
        inputs = [
            path for path in case_dir.iterdir() if path.name.startswith("input.") and path.is_file()
        ]
        if len(inputs) != 1:
            raise ValueError(f"{case_dir} needs exactly one input.* file")
        cases.append(EvalCase(name=case_dir.name, input_path=inputs[0], expected=expected))
    return cases


def _total_hundredths(reading: TimesheetReading) -> int | None:
    total, _ = checks.check_hours(reading)
    return total


def _approval_kind(reading: TimesheetReading) -> ApprovalKind:
    approval = reading.approval.value
    return approval.kind if approval else ApprovalKind.NONE


def _names_equal(actual: str | None, expected: str | None) -> bool:
    if actual is None or expected is None:
        return actual == expected
    return checks.names_match(actual, expected)


def _codes_for(reading: TimesheetReading) -> set[str]:
    """The document-only checks: hours, approval, and confidence."""
    _, hour_findings = checks.check_hours(reading)
    findings = hour_findings + checks.check_approval(reading) + checks.check_confidence(reading)
    return {finding.code.value for finding in findings}


def score_case(report: EvalReport, case: EvalCase, actual: TimesheetReading) -> None:
    expected = case.expected.reading
    report.cases += 1

    outcomes = {
        "consultant_name": _names_equal(
            actual.consultant_name.value, expected.consultant_name.value
        ),
        "client_name": _names_equal(actual.client_name.value, expected.client_name.value),
        "period_start": actual.period_start.value == expected.period_start.value,
        "period_end": actual.period_end.value == expected.period_end.value,
        "approval_kind": _approval_kind(actual) is _approval_kind(expected),
    }
    actual_total, expected_total = _total_hundredths(actual), _total_hundredths(expected)
    outcomes["total_hours"] = actual_total == expected_total
    if actual_total is not None and expected_total is not None:
        report.hours_cases += 1
        report.hours_error_hundredths_total += abs(actual_total - expected_total)

    for field_name, correct in outcomes.items():
        if correct:
            report.field_correct[field_name] = report.field_correct.get(field_name, 0) + 1
        else:
            report.failures.append(f"{case.name}: {field_name}")

    if _codes_for(actual) == set(case.expected.review_codes):
        report.codes_exact += 1
    else:
        report.failures.append(f"{case.name}: review codes")


def run_eval(cases: list[EvalCase], read: Callable[[EvalCase], TimesheetReading]) -> EvalReport:
    report = EvalReport()
    for case in cases:
        score_case(report, case, read(case))
    return report


class Thresholds(BaseModel):
    """The recorded floor the reader must stay at or above (docs/roadmap.md PR 6)."""

    model_config = ConfigDict(frozen=True)

    field_accuracy_percent: dict[str, int]
    mean_hours_error_hundredths_max: int
    codes_accuracy_percent: int


def below_thresholds(report: EvalReport, thresholds: Thresholds) -> list[str]:
    problems: list[str] = []
    for field_name, minimum in thresholds.field_accuracy_percent.items():
        actual = report.field_accuracy_percent_floor(field_name)
        if actual < minimum:
            problems.append(f"{field_name} accuracy {actual}% is below {minimum}%")
    error = report.mean_hours_error_hundredths_ceiling()
    if error > thresholds.mean_hours_error_hundredths_max:
        problems.append(
            f"mean hours error {error} hundredths is above"
            f" {thresholds.mean_hours_error_hundredths_max}"
        )
    codes = report.codes_accuracy_percent_floor()
    if codes < thresholds.codes_accuracy_percent:
        problems.append(
            f"review-code accuracy {codes}% is below {thresholds.codes_accuracy_percent}%"
        )
    return problems
