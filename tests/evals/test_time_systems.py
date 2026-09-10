"""Every client time system Icon bills through needs a case in the set.

The reader is only proven on the layouts it will actually meet. A made-up CSV
says nothing about what a real export from a real time system looks like, so
each system named in `time_systems.json` must have at least one anonymised real
timesheet in the set (docs/roadmap.md PR 12). The list is empty until Kevin
answers which systems his clients use; adding one without adding a case fails
here rather than being noticed after the first live cycle.
"""

import json
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from finance_ops_agent.application.eval_runner import (
    INVENTED,
    CaseMeta,
    load_cases,
    missing_time_systems,
    time_systems_covered,
)

CASES_DIR = Path(__file__).parent / "timesheets"
TIME_SYSTEMS = Path(__file__).parent / "time_systems.json"


def required_systems() -> list[str]:
    listed = json.loads(TIME_SYSTEMS.read_text())["required"]
    assert isinstance(listed, list)
    return [str(system) for system in listed]


def test_every_required_time_system_has_a_case() -> None:
    cases = load_cases(CASES_DIR)
    missing = missing_time_systems(cases, required_systems())
    assert missing == [], (
        f"no timesheet in the set came from: {', '.join(missing)}."
        " Add an anonymised real sample per system (docs/roadmap.md PR 12)."
    )


def test_the_set_lists_the_time_systems_it_covers() -> None:
    """The listing itself: what the set proves the reader on, by system."""
    covered = time_systems_covered(load_cases(CASES_DIR))
    assert covered, "the set covers no cases at all"
    for system, names in covered.items():
        assert names, system
    # Until Kevin supplies real samples, every case is made-up, and the set
    # says so rather than implying coverage it does not have.
    if not required_systems():
        assert set(covered) == {INVENTED}, (
            "a case names a real time system, but time_systems.json requires none;"
            " add it to the required list so it is checked for"
        )


def test_a_real_sample_must_name_its_system() -> None:
    """A real timesheet with no system named would prove nothing in particular."""
    for case in load_cases(CASES_DIR):
        if case.meta.is_real():
            assert case.meta.time_system != INVENTED, case.name
            assert case.meta.anonymised_by, case.name
            assert case.meta.anonymised_on, case.name


def test_calling_a_case_real_without_saying_who_anonymised_it_is_refused() -> None:
    """The record of the check is the only evidence the check happened."""
    with pytest.raises(ValidationError):
        CaseMeta(time_system="Workday", origin="anonymised_real")
    with pytest.raises(ValidationError):
        # A real sample that will not say which system it came from.
        CaseMeta(origin="anonymised_real", anonymised_by="Ash", anonymised_on=date(2026, 9, 10))


def test_a_made_up_case_needs_no_paperwork() -> None:
    meta = CaseMeta()
    assert meta.time_system == INVENTED
    assert not meta.is_real()


def test_the_real_format_samples_are_in_the_set() -> None:
    """The formats Icon actually receives, and what each one alone can prove."""
    by_name = {case.name: case for case in load_cases(CASES_DIR)}
    real = {name: case for name, case in by_name.items() if case.meta.is_real_format()}
    assert len(real) == 4, sorted(real)

    # A timesheet alone: approved, but a week runs past the month end.
    timesheet = by_name["45-pdf-mastec-timesheet-jul"]
    assert "PART_WEEK_UNCLEAR" in timesheet.expected.review_codes
    assert "NO_APPROVAL" not in timesheet.expected.review_codes

    # The vendor's invoice alone: a printed total, but nothing shows approval.
    invoice = by_name["47-pdf-startech-invoice-jul"]
    assert "NO_APPROVAL" in invoice.expected.review_codes
    assert "PART_WEEK_UNCLEAR" not in invoice.expected.review_codes
    assert invoice.expected.reading.stated_total_hours_hundredths.value == 17600


def test_a_real_format_sample_needs_no_anonymising_paperwork() -> None:
    """Nothing in it was ever real, so there was nothing to check it for."""
    for case in load_cases(CASES_DIR):
        if case.meta.origin == "real_format_invented_data":
            assert case.meta.anonymised_by is None, case.name
            assert case.meta.time_system != INVENTED, case.name
