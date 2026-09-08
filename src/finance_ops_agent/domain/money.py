"""Money as whole cents and hours as whole hundredths of an hour.

Never floats, and never Decimal in the database (see docs/technical-design.md).
Rates are entered in the engagement list as dollars with cents ("140.00");
parsing goes straight from text to integers.
"""

import re
from dataclasses import dataclass

_MONEY_RE = re.compile(r"\$?\s*(\d{1,3}(?:,\d{3})*|\d+)(?:\.(\d{1,2}))?")
_HOURS_RE = re.compile(r"(\d+)(?:\.(\d{1,2}))?")


def round_half_up(numerator: int, denominator: int) -> int:
    """Divide, rounding an exact half away from zero. Amounts are never negative here."""
    if numerator < 0:
        raise ValueError(f"numerator must not be negative, got {numerator}")
    if denominator <= 0:
        raise ValueError(f"denominator must be positive, got {denominator}")
    return (numerator + denominator // 2) // denominator


@dataclass(frozen=True)
class Money:
    cents: int

    def __post_init__(self) -> None:
        if self.cents < 0:
            raise ValueError(f"money must not be negative, got {self.cents} cents")

    @classmethod
    def parse(cls, text: str) -> "Money":
        """Parse dollars-and-cents text like "140", "140.5", "$1,140.00"."""
        match = _MONEY_RE.fullmatch(text.strip())
        if match is None:
            raise ValueError(f"not an amount of money: {text!r}")
        dollars = int(match.group(1).replace(",", ""))
        cents = int((match.group(2) or "").ljust(2, "0") or 0)
        return cls(dollars * 100 + cents)

    def __add__(self, other: "Money") -> "Money":
        return Money(self.cents + other.cents)

    def __str__(self) -> str:
        """Dollars with cents and thousands separators, as Kevin writes them: 21,840.00"""
        return f"{self.cents // 100:,}.{self.cents % 100:02d}"


@dataclass(frozen=True)
class Hours:
    hundredths: int

    def __post_init__(self) -> None:
        if self.hundredths < 0:
            raise ValueError(f"hours must not be negative, got {self.hundredths} hundredths")

    @classmethod
    def parse(cls, text: str) -> "Hours":
        """Parse hours text like "156", "7.5", "156.25"."""
        match = _HOURS_RE.fullmatch(text.strip())
        if match is None:
            raise ValueError(f"not a number of hours: {text!r}")
        whole = int(match.group(1))
        hundredths = int((match.group(2) or "").ljust(2, "0") or 0)
        return cls(whole * 100 + hundredths)

    def __add__(self, other: "Hours") -> "Hours":
        return Hours(self.hundredths + other.hundredths)

    def __str__(self) -> str:
        return f"{self.hundredths // 100}.{self.hundredths % 100:02d}"


def invoice_amount(approved_hours: Hours, bill_rate: Money) -> Money:
    """What the client is invoiced: approved hours x bill rate."""
    return Money(round_half_up(approved_hours.hundredths * bill_rate.cents, 100))


def pay_amount(approved_hours: Hours, pay_rate: Money) -> Money:
    """What the consultant or vendor company is owed: approved hours x pay rate."""
    return Money(round_half_up(approved_hours.hundredths * pay_rate.cents, 100))
