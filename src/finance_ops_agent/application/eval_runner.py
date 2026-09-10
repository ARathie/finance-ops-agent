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
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from finance_ops_agent.domain import checks
from finance_ops_agent.domain.reading import ApprovalKind, TimesheetReading
from finance_ops_agent.ports.reader import TokenUsage

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


INVENTED = "invented"
"""The `time_system` of a made-up case: no client's real system."""


class CaseMeta(BaseModel):
    """Where a case came from, in its own `meta.json`. Absent means made-up.

    `time_system` is the client time system the timesheet was produced by, so
    the set can be checked for a case per system Icon actually bills through
    (docs/roadmap.md PR 12). Icon often will not know the product's name --
    every client and consultant may use a different one -- so the label names
    the pairing whose format repeats month after month, not a product.

    Three origins, because the samples differ in what they prove and in what
    care they need:

    - `invented`: made up entirely, layout included. Proves the reader on a
      shape nobody has ever received.
    - `real_format_invented_data`: a genuine export or document from a real
      system, carrying invented names, rates and hours. Proves the reader on a
      layout Icon actually receives, and needs no anonymising because nothing
      in it was ever real.
    - `anonymised_real`: a real document with the identifying details replaced.
      Must record who checked it and when: that record is the only evidence
      anyone confirmed nothing identifying was left before it was committed.
    """

    model_config = ConfigDict(frozen=True)

    time_system: str = INVENTED
    origin: Literal["invented", "real_format_invented_data", "anonymised_real"] = "invented"
    anonymised_by: str | None = None
    anonymised_on: date | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _real_samples_record_who_checked_them(self) -> "CaseMeta":
        if self.origin != "anonymised_real":
            return self
        if self.time_system == INVENTED:
            raise ValueError('an anonymised real case must name the "time_system" it came from')
        missing = [
            name for name in ("anonymised_by", "anonymised_on") if getattr(self, name) is None
        ]
        if missing:
            raise ValueError("an anonymised real case must record " + ", ".join(missing))
        return self

    def is_real(self) -> bool:
        """Was this taken from a document someone really received?"""
        return self.origin == "anonymised_real"

    def is_real_format(self) -> bool:
        """Does this prove the reader on a layout Icon actually receives?"""
        return self.origin in ("anonymised_real", "real_format_invented_data")


@dataclass(frozen=True)
class EvalCase:
    name: str
    input_path: Path
    expected: ExpectedCase
    meta: CaseMeta = field(default_factory=CaseMeta)


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
        meta_file = case_dir / "meta.json"
        meta = (
            CaseMeta.model_validate(json.loads(meta_file.read_text()))
            if meta_file.exists()
            else CaseMeta()
        )
        cases.append(
            EvalCase(name=case_dir.name, input_path=inputs[0], expected=expected, meta=meta)
        )
    return cases


def time_systems_covered(cases: list[EvalCase]) -> dict[str, list[str]]:
    """Every time system in the set, and the cases that came from it."""
    covered: dict[str, list[str]] = {}
    for case in cases:
        covered.setdefault(case.meta.time_system, []).append(case.name)
    return covered


def missing_time_systems(cases: list[EvalCase], required: list[str]) -> list[str]:
    """The systems Icon bills through that no case in the set covers."""
    covered = time_systems_covered(cases)
    return [system for system in required if not covered.get(system)]


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


def run_eval_by_origin(
    cases: list[EvalCase], read: Callable[[EvalCase], TimesheetReading]
) -> dict[str, EvalReport]:
    """Score the made-up cases and the real-format ones separately.

    Blending them hides the number that matters. The invented cases are a
    regression net written to a shape someone believed in; only the cases taken
    from documents Icon actually receives say whether the reader can do the job
    (docs/roadmap.md PR 12).
    """
    groups: dict[str, list[EvalCase]] = {}
    for case in cases:
        key = "real formats" if case.meta.is_real_format() else "made-up"
        groups.setdefault(key, []).append(case)
    return {name: run_eval(group, read) for name, group in groups.items()}


def run_eval(cases: list[EvalCase], read: Callable[[EvalCase], TimesheetReading]) -> EvalReport:
    report = EvalReport()
    for case in cases:
        score_case(report, case, read(case))
    return report


class Thresholds(BaseModel):
    """The recorded floor the reader must stay at or above (docs/roadmap.md PR 6).

    `source` says where the recorded answers the floor was set from came from.
    `bootstrap` means every `recorded.json` is a copy of its `expected.json`, so
    the scores are perfect by construction and prove the harness, not the
    reading. `live` means a real `fops eval --live` wrote them, and then the
    model, the prompt version, and the date of that run are recorded with it.
    """

    # `model` is a field name here, so pydantic's `model_` namespace is released.
    model_config = ConfigDict(frozen=True, protected_namespaces=())

    field_accuracy_percent: dict[str, int]
    mean_hours_error_hundredths_max: int
    codes_accuracy_percent: int
    source: Literal["bootstrap", "live"] = "bootstrap"
    model: str | None = None
    prompt_version: str | None = None
    recorded_on: date | None = None

    @model_validator(mode="after")
    def _live_answers_say_where_they_came_from(self) -> "Thresholds":
        if self.source != "live":
            return self
        missing = [
            name
            for name in ("model", "prompt_version", "recorded_on")
            if getattr(self, name) is None
        ]
        if missing:
            raise ValueError(
                'thresholds with "source": "live" must also record '
                + ", ".join(missing)
                + " (written by `fops eval --live`)"
            )
        return self

    def proves_the_harness_only(self) -> bool:
        """True while the recorded answers are still copies of the expected ones."""
        return self.source == "bootstrap"


BOOTSTRAP_WARNING = (
    "These scores come from bootstrap recorded answers: every recorded.json is a"
    " copy of its expected.json, so the numbers prove the harness, not the"
    " reading. Run `fops eval --live` with a real ANTHROPIC_API_KEY to replace"
    " them (docs/roadmap.md PR 12)."
)


def stamp_live_provenance(
    thresholds: Thresholds, model: str, prompt_version: str, recorded_on: date
) -> Thresholds:
    """The same floor, marked as measured against a real run of the model."""
    return thresholds.model_copy(
        update={
            "source": "live",
            "model": model,
            "prompt_version": prompt_version,
            "recorded_on": recorded_on,
        }
    )


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


class ModelPrices(BaseModel):
    """List prices, in whole cents per million tokens.

    Cents per million rather than dollars per million so the arithmetic below
    stays in integers: no floats anywhere near a number that becomes money
    (CLAUDE.md rule 6). Cached input is billed at two different rates - writing
    a prompt into the cache costs more than plain input, reading it back costs
    far less - so the two are counted separately.
    """

    model_config = ConfigDict(frozen=True)

    input_cents_per_million: int
    output_cents_per_million: int
    cache_write_cents_per_million: int
    cache_read_cents_per_million: int


PRICES_CHECKED_ON = date(2026, 6, 24)
"""When the table below was last read off Anthropic's published prices.

Prices change. `fops eval --live --price-*` overrides them without a code
change, and the estimate always prints this date next to the number so nobody
quotes a stale figure as fact.
"""

MODEL_PRICES = {
    # Claude Opus 5: $5.00 per million input tokens, $25.00 per million output.
    # Cache writes are 1.25x input, cache reads 0.1x input.
    "claude-opus-5": ModelPrices(
        input_cents_per_million=500,
        output_cents_per_million=2_500,
        cache_write_cents_per_million=625,
        cache_read_cents_per_million=50,
    ),
}


def cost_millicents(usage: TokenUsage, prices: ModelPrices) -> int:
    """What those tokens cost, in thousandths of a cent, rounded down.

    Thousandths because one timesheet costs a few cents: in whole cents the
    per-timesheet figure the roadmap asks for would round to nothing.
    """
    return (
        usage.input_tokens * prices.input_cents_per_million
        + usage.output_tokens * prices.output_cents_per_million
        + usage.cache_creation_input_tokens * prices.cache_write_cents_per_million
        + usage.cache_read_input_tokens * prices.cache_read_cents_per_million
    ) // 1_000


def format_dollars(millicents: int) -> str:
    """Thousandths of a cent as dollars to four places, e.g. 4_250 -> "$0.0425"."""
    ten_thousandths = (millicents + 5) // 10
    return f"${ten_thousandths // 10_000}.{ten_thousandths % 10_000:04d}"


@dataclass(frozen=True)
class UsageReport:
    """What a live run spent, and what it says about one timesheet."""

    usage: TokenUsage
    cases: int
    prices: ModelPrices | None
    model: str

    def per_case(self, total: int) -> int:
        return total // self.cases if self.cases else 0

    def total_millicents(self) -> int | None:
        return None if self.prices is None else cost_millicents(self.usage, self.prices)

    def format(self) -> str:
        usage = self.usage
        lines = [
            f"{usage.requests} model call(s) over {self.cases} timesheet(s), {self.model}:",
            f"  input tokens: {usage.input_tokens}"
            f" (+{usage.cache_creation_input_tokens} written to cache,"
            f" {usage.cache_read_input_tokens} read from cache)",
            f"  output tokens: {usage.output_tokens}",
            f"  per timesheet: {self.per_case(usage.total_input_tokens)} in,"
            f" {self.per_case(usage.output_tokens)} out",
        ]
        total = self.total_millicents()
        if total is None:
            lines.append(
                f"  cost: no price recorded for {self.model};"
                " pass --price-input and --price-output to estimate it"
            )
        else:
            lines.append(
                f"  cost: {format_dollars(total)} in total,"
                f" {format_dollars(self.per_case(total))} per timesheet"
                f" (list prices as of {PRICES_CHECKED_ON.isoformat()})"
            )
        return "\n".join(lines)
