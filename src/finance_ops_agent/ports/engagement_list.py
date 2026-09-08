"""The port for reading Kevin's engagement list.

The adapter only reads cells as text with their row numbers; turning them into
typed rows and problems is domain code (finance_ops_agent.domain.engagements).
The agent never writes to the workbook.
"""

from typing import Protocol

from finance_ops_agent.domain.engagements import RawWorkbook


class EngagementList(Protocol):
    def load(self) -> RawWorkbook:
        """Read all four sheets. Raises OSError when the file cannot be opened."""
        ...
