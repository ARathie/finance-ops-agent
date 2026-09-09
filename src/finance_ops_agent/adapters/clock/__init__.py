"""The real clock: today in Icon's timezone, timestamps in UTC."""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo


class SystemClock:
    """`today()` in Icon's timezone (FOPS_TIMEZONE), `now()` always UTC.

    The timezone is required rather than defaulted: a period boundary or a due
    date computed in the wrong zone is off by a day (docs/open-questions.md).
    """

    def __init__(self, timezone: str) -> None:
        self._zone = ZoneInfo(timezone)

    def today(self) -> date:
        return datetime.now(self._zone).date()

    def now(self) -> datetime:
        return datetime.now(UTC)
