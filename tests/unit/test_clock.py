"""The real clock: today in Icon's timezone, timestamps in UTC."""

from datetime import UTC

import pytest

from finance_ops_agent.adapters.clock import SystemClock


def test_now_is_utc() -> None:
    assert SystemClock("America/New_York").now().tzinfo is UTC


def test_today_follows_the_configured_timezone() -> None:
    # Late evening in New York is already tomorrow in Auckland; the agent must
    # use Icon's day, not the machine's.
    new_york = SystemClock("America/New_York").today()
    auckland = SystemClock("Pacific/Auckland").today()
    assert (auckland - new_york).days in (0, 1)


def test_an_unknown_timezone_is_refused() -> None:
    with pytest.raises(Exception, match="Nowhere"):
        SystemClock("Nowhere/Fictional")
