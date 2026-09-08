"""A frozen clock for tests and the fake dry run."""

from datetime import UTC, date, datetime, time


class FakeClock:
    def __init__(self, today: date, now: datetime | None = None) -> None:
        self._today = today
        self._now = now or datetime.combine(today, time(12, 0), tzinfo=UTC)

    def today(self) -> date:
        return self._today

    def now(self) -> datetime:
        return self._now
