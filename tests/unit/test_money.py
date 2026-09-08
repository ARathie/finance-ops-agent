import pytest
from hypothesis import given
from hypothesis import strategies as st

from finance_ops_agent.domain.money import (
    Hours,
    Money,
    invoice_amount,
    pay_amount,
    round_half_up,
)


class TestWorkedExample:
    """156 hours at $140 bill / $100 pay -> $21,840 invoiced, $15,600 owed.

    The worked example from docs/context/04_business_rules_and_edge_cases.md.
    """

    def test_invoice_amount(self) -> None:
        amount = invoice_amount(Hours.parse("156"), Money.parse("140.00"))
        assert amount == Money(2_184_000)
        assert str(amount) == "21,840.00"

    def test_pay_amount(self) -> None:
        amount = pay_amount(Hours.parse("156"), Money.parse("100.00"))
        assert amount == Money(1_560_000)
        assert str(amount) == "15,600.00"


class TestRoundHalfUp:
    @pytest.mark.parametrize(
        ("numerator", "denominator", "expected"),
        [
            (0, 100, 0),
            (49, 100, 0),
            (50, 100, 1),  # an exact half rounds up
            (51, 100, 1),
            (149, 100, 1),
            (150, 100, 2),
            (218_400_000, 100, 2_184_000),
        ],
    )
    def test_examples(self, numerator: int, denominator: int, expected: int) -> None:
        assert round_half_up(numerator, denominator) == expected

    def test_rejects_negative_numerator(self) -> None:
        with pytest.raises(ValueError):
            round_half_up(-1, 100)

    def test_rejects_zero_denominator(self) -> None:
        with pytest.raises(ValueError):
            round_half_up(1, 0)

    @given(st.integers(min_value=0, max_value=10**12))
    def test_never_off_by_more_than_half_a_cent(self, numerator: int) -> None:
        result = round_half_up(numerator, 100)
        assert 2 * abs(result * 100 - numerator) <= 100

    @given(st.integers(min_value=0, max_value=10**12))
    def test_exact_when_divisible(self, hundreds: int) -> None:
        assert round_half_up(hundreds * 100, 100) == hundreds

    @given(st.integers(min_value=0, max_value=10**12))
    def test_exact_half_rounds_up(self, hundreds: int) -> None:
        assert round_half_up(hundreds * 100 + 50, 100) == hundreds + 1


class TestAmountProperties:
    @given(
        st.integers(min_value=0, max_value=10**6),
        st.integers(min_value=0, max_value=10**6),
        st.integers(min_value=0, max_value=10**6),
    )
    def test_more_hours_never_bills_less(self, fewer: int, more: int, rate_cents: int) -> None:
        low, high = sorted((fewer, more))
        rate = Money(rate_cents)
        assert invoice_amount(Hours(low), rate).cents <= invoice_amount(Hours(high), rate).cents

    @given(
        st.integers(min_value=0, max_value=10**6),
        st.integers(min_value=0, max_value=10**6),
    )
    def test_whole_hours_are_exact(self, whole_hours: int, rate_cents: int) -> None:
        # 100 hundredths per hour cancels the divisor exactly.
        amount = invoice_amount(Hours(whole_hours * 100), Money(rate_cents))
        assert amount.cents == whole_hours * rate_cents

    @given(
        st.integers(min_value=0, max_value=10**6),
        st.integers(min_value=0, max_value=10**6),
    )
    def test_pay_and_invoice_use_the_same_arithmetic(
        self, hundredths: int, rate_cents: int
    ) -> None:
        hours, rate = Hours(hundredths), Money(rate_cents)
        assert pay_amount(hours, rate) == invoice_amount(hours, rate)


class TestMoneyValue:
    @pytest.mark.parametrize(
        ("text", "cents"),
        [
            ("140", 14_000),
            ("140.00", 14_000),
            ("140.5", 14_050),
            ("140.25", 14_025),
            ("1,140.25", 114_025),
            ("$140.00", 14_000),
            (" $ 21,840.00 ", 2_184_000),
            ("0", 0),
        ],
    )
    def test_parse(self, text: str, cents: int) -> None:
        assert Money.parse(text) == Money(cents)

    @pytest.mark.parametrize("text", ["", "abc", "-5", "140.005", "1,40.00", "140.00.00"])
    def test_parse_rejects(self, text: str) -> None:
        with pytest.raises(ValueError):
            Money.parse(text)

    def test_rejects_negative(self) -> None:
        with pytest.raises(ValueError):
            Money(-1)

    def test_add(self) -> None:
        assert Money(150) + Money(75) == Money(225)

    @pytest.mark.parametrize(
        ("cents", "text"),
        [(0, "0.00"), (5, "0.05"), (14_050, "140.50"), (2_184_000, "21,840.00")],
    )
    def test_str(self, cents: int, text: str) -> None:
        assert str(Money(cents)) == text


class TestHoursValue:
    @pytest.mark.parametrize(
        ("text", "hundredths"),
        [("156", 15_600), ("156.00", 15_600), ("7.5", 750), ("156.25", 15_625), ("0", 0)],
    )
    def test_parse(self, text: str, hundredths: int) -> None:
        assert Hours.parse(text) == Hours(hundredths)

    @pytest.mark.parametrize("text", ["", "abc", "-5", "7.125"])
    def test_parse_rejects(self, text: str) -> None:
        with pytest.raises(ValueError):
            Hours.parse(text)

    def test_rejects_negative(self) -> None:
        with pytest.raises(ValueError):
            Hours(-1)

    def test_add(self) -> None:
        assert Hours(750) + Hours(425) == Hours(1175)

    def test_str(self) -> None:
        assert str(Hours(15_600)) == "156.00"
