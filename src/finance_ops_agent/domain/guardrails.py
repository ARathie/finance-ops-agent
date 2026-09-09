"""What has to be true before a billing email goes out without asking Kevin.

All five conditions from docs/technical-design.md, and every one of them is a
reason to fall back to asking rather than to stop: the item still gets invoiced,
Kevin just sees it first. `dry_run` is checked before any of this and overrides
everything, so the kill switch cannot be reasoned around.
"""

from dataclasses import dataclass

from finance_ops_agent.domain.money import Money
from finance_ops_agent.domain.reading import Confidence, TimesheetReading

# How far this invoice may differ from the recent ones for the same engagement
# before a person should look at it.
UNUSUAL_AMOUNT_PERCENT = 25
RECENT_INVOICES_CONSIDERED = 3


@dataclass(frozen=True)
class GuardrailCheck:
    """Whether this item may send automatically, and if not, why not."""

    may_send_automatically: bool
    reasons: tuple[str, ...] = ()

    def why_not(self) -> str:
        return "; ".join(self.reasons)


def amount_is_unusual(amount: Money, recent: list[Money]) -> bool:
    """More than 25% away from the average of the last few invoices.

    With no history there is nothing to compare against, so nothing is unusual:
    the other guardrails still apply.
    """
    considered = recent[-RECENT_INVOICES_CONSIDERED:]
    if not considered:
        return False
    average_cents = sum(entry.cents for entry in considered) // len(considered)
    if average_cents == 0:
        return amount.cents != 0
    difference = abs(amount.cents - average_cents) * 100
    return difference > average_cents * UNUSUAL_AMOUNT_PERCENT


def confidence_is_all_high(reading: TimesheetReading) -> bool:
    """Consultant, dates, hours, and approval must all have been clearly read."""
    hours_confidence = (
        reading.stated_total_hours_hundredths.confidence
        if reading.stated_total_hours_hundredths.value is not None
        else reading.daily_entries.confidence
    )
    return all(
        confidence is Confidence.HIGH
        for confidence in (
            reading.consultant_name.confidence,
            reading.period_start.confidence,
            reading.period_end.confidence,
            hours_confidence,
            reading.approval.confidence,
        )
    )


def check_guardrails(
    *,
    send_automatically: bool,
    open_review_count: int,
    readings: list[TimesheetReading],
    amount: Money,
    recent_amounts: list[Money],
) -> GuardrailCheck:
    """Every condition, with a plain reason for each one that does not hold."""
    reasons: list[str] = []
    if not send_automatically:
        reasons.append('the engagement row does not say "Send automatically"')
    if open_review_count:
        reasons.append(f"{open_review_count} thing(s) still need your review")
    if not readings:
        reasons.append("I have no reading of the timesheet to check")
    elif not all(confidence_is_all_high(reading) for reading in readings):
        reasons.append("I was not completely sure about something I read")
    if amount_is_unusual(amount, recent_amounts):
        reasons.append(
            f"${amount} is more than {UNUSUAL_AMOUNT_PERCENT}% away from the recent"
            " invoices for this engagement"
        )
    return GuardrailCheck(not reasons, tuple(reasons))
