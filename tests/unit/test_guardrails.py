"""Every guardrail from docs/technical-design.md, one test each."""

from datetime import date

import pytest

from finance_ops_agent.domain.guardrails import (
    UNUSUAL_AMOUNT_PERCENT,
    GuardrailCheck,
    amount_is_unusual,
    check_guardrails,
    confidence_is_all_high,
)
from finance_ops_agent.domain.money import Money
from finance_ops_agent.domain.reading import Confidence, TimesheetReading
from tests.scenarios.conftest import reading

AUG = (date(2026, 8, 1), date(2026, 8, 31))
CLEAN = [reading(*AUG)]
AMOUNT = Money(2_184_000)


def check(
    *,
    send_automatically: bool = True,
    open_review_count: int = 0,
    readings: list[TimesheetReading] | None = None,
    amount: Money = AMOUNT,
    recent_amounts: list[Money] | None = None,
) -> GuardrailCheck:
    """Everything holding, except whatever this test names."""
    return check_guardrails(
        send_automatically=send_automatically,
        open_review_count=open_review_count,
        readings=CLEAN if readings is None else readings,
        amount=amount,
        recent_amounts=recent_amounts or [],
    )


def test_everything_holding_means_it_may_send() -> None:
    result = check()
    assert result.may_send_automatically
    assert result.reasons == ()


class TestEachGuardrailBlocks:
    def test_the_engagement_row_must_say_send_automatically(self) -> None:
        result = check(send_automatically=False)
        assert not result.may_send_automatically
        assert "Send automatically" in result.why_not()

    def test_an_open_review_blocks_it(self) -> None:
        result = check(open_review_count=1)
        assert not result.may_send_automatically
        assert "need your review" in result.why_not()

    @pytest.mark.parametrize("confidence", [Confidence.MEDIUM, Confidence.LOW])
    def test_anything_less_than_high_confidence_blocks_it(self, confidence: Confidence) -> None:
        result = check(readings=[reading(*AUG, confidence=confidence)])
        assert not result.may_send_automatically
        assert "not completely sure" in result.why_not()

    def test_no_reading_at_all_blocks_it(self) -> None:
        result = check(readings=[])
        assert not result.may_send_automatically

    def test_an_unusual_amount_blocks_it(self) -> None:
        # Recent invoices around $10,000; this one is $21,840.
        result = check(recent_amounts=[Money(1_000_000), Money(1_000_000)])
        assert not result.may_send_automatically
        assert f"{UNUSUAL_AMOUNT_PERCENT}%" in result.why_not()

    def test_several_problems_are_all_reported(self) -> None:
        result = check(send_automatically=False, open_review_count=2, readings=[])
        assert len(result.reasons) == 3


class TestUnusualAmount:
    def test_no_history_means_nothing_to_compare(self) -> None:
        assert not amount_is_unusual(Money(2_184_000), [])

    def test_within_25_percent_is_fine(self) -> None:
        average = [Money(2_000_000)]
        assert not amount_is_unusual(Money(2_400_000), average)  # +20%
        assert not amount_is_unusual(Money(1_600_000), average)  # -20%

    def test_more_than_25_percent_either_way_is_unusual(self) -> None:
        average = [Money(2_000_000)]
        assert amount_is_unusual(Money(2_600_000), average)  # +30%
        assert amount_is_unusual(Money(1_400_000), average)  # -30%

    def test_exactly_25_percent_is_still_fine(self) -> None:
        assert not amount_is_unusual(Money(2_500_000), [Money(2_000_000)])

    def test_only_the_last_three_count(self) -> None:
        # An ancient tiny invoice must not drag the average down forever.
        history = [Money(10_000), Money(2_000_000), Money(2_000_000), Money(2_000_000)]
        assert not amount_is_unusual(Money(2_100_000), history)

    def test_a_zero_history_makes_any_amount_unusual(self) -> None:
        assert amount_is_unusual(Money(2_184_000), [Money(0)])
        assert not amount_is_unusual(Money(0), [Money(0)])


class TestConfidence:
    def test_all_high_passes(self) -> None:
        assert confidence_is_all_high(reading(*AUG))

    def test_daily_hours_confidence_is_used_when_there_is_no_printed_total(self) -> None:
        low_dailies = reading(
            *AUG,
            total_hundredths=None,
            dailies=[(date(2026, 8, 3), 800)],
            confidence=Confidence.HIGH,
        )
        assert confidence_is_all_high(low_dailies)
