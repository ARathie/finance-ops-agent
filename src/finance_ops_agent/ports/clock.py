"""The clock port: "today" in Icon's timezone, and the current UTC moment.

Domain code never reads the system clock; anything date-driven takes today as
an argument, and the application gets it from here.
"""

from datetime import date, datetime
from typing import Protocol


class Clock(Protocol):
    def today(self) -> date: ...

    def now(self) -> datetime: ...
