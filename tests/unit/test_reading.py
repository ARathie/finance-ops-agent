from datetime import date

import pytest
from pydantic import ValidationError

from finance_ops_agent.domain.reading import (
    Approval,
    ApprovalKind,
    Confidence,
    DailyEntry,
    ReadField,
    TimesheetReading,
)


def full_reading() -> TimesheetReading:
    """A reading like the worked example: Priya Shah, Acme Corp, August 2026, 156 hours."""
    days = [DailyEntry(day=date(2026, 8, day), hours_hundredths=750) for day in range(3, 23)]
    days.append(DailyEntry(day=date(2026, 8, 24), hours_hundredths=15_600 - 20 * 750))
    return TimesheetReading(
        consultant_name=ReadField[str](
            value="Priya Shah", quote="Consultant: Priya Shah", confidence=Confidence.HIGH
        ),
        client_name=ReadField[str](
            value="Acme Corp", quote="Client: Acme Corp", confidence=Confidence.HIGH
        ),
        end_client_name=ReadField[str](confidence=Confidence.LOW),
        period_start=ReadField[date](
            value=date(2026, 8, 1), quote="08/01/2026", confidence=Confidence.HIGH
        ),
        period_end=ReadField[date](
            value=date(2026, 8, 31), quote="08/31/2026", confidence=Confidence.HIGH
        ),
        daily_entries=ReadField[list[DailyEntry]](
            value=days, quote="7.50 each weekday", confidence=Confidence.MEDIUM
        ),
        stated_total_hours_hundredths=ReadField[int](
            value=15_600, quote="Total: 156.00", confidence=Confidence.HIGH
        ),
        approval=ReadField[Approval](
            value=Approval(
                kind=ApprovalKind.APPROVER_NAME_DATE,
                approver="Jane Doe",
                approval_date=date(2026, 9, 2),
            ),
            quote="Approved by Jane Doe, 9/2/2026",
            confidence=Confidence.HIGH,
        ),
        unusual_items=["one Saturday worked"],
    )


def test_a_whole_form_validates() -> None:
    reading = full_reading()
    assert reading.consultant_name.value == "Priya Shah"
    assert reading.approval.value is not None
    assert reading.approval.value.kind is ApprovalKind.APPROVER_NAME_DATE


def test_code_sums_the_daily_hours_itself() -> None:
    assert full_reading().summed_daily_hundredths() == 15_600


def test_no_daily_hours_sums_to_none() -> None:
    reading = full_reading().model_copy(
        update={"daily_entries": ReadField[list[DailyEntry]](confidence=Confidence.LOW)}
    )
    assert reading.summed_daily_hundredths() is None


def test_every_field_is_required() -> None:
    with pytest.raises(ValidationError):
        TimesheetReading()  # type: ignore[call-arg]


def test_negative_daily_hours_are_rejected() -> None:
    with pytest.raises(ValidationError):
        DailyEntry(day=date(2026, 8, 3), hours_hundredths=-1)


def test_unknown_confidence_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ReadField[str].model_validate({"value": "x", "confidence": "certain"})


def test_the_form_is_frozen() -> None:
    reading = full_reading()
    with pytest.raises(ValidationError):
        reading.unusual_items = []
