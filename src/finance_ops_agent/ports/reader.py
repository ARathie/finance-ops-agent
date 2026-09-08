"""The port for reading timesheets (Claude, in PR 6; a scripted fake until then).

The reader only fills in the TimesheetReading form. It never sums hours, never
sees a rate, and never picks recipients. `classify` and `read_reply` join this
port with the Claude adapter (see docs/integrations/claude-extraction.md).
"""

from typing import Protocol

from finance_ops_agent.domain.reading import ReadingHints, TimesheetReading


class CantReadAttachmentError(Exception):
    """The attachment could not be read into a whole form (CANT_READ_ATTACHMENT)."""


class TimesheetReader(Protocol):
    @property
    def model_name(self) -> str:
        """Recorded with every reading (e.g. "claude-opus-5", or "fake")."""
        ...

    @property
    def prompt_version(self) -> str:
        """The prompt file version recorded with every reading (e.g. "timesheet_v1")."""
        ...

    def read_timesheet(
        self,
        content: bytes,
        filename: str,
        mime_type: str,
        hints: ReadingHints | None = None,
    ) -> TimesheetReading:
        """Fill in the whole form, or raise CantReadAttachmentError. Never partial."""
        ...
