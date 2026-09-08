"""The port for reading timesheets (Claude, in PR 6; a scripted fake until then).

The reader only fills in the TimesheetReading form. It never sums hours, never
sees a rate, and never picks recipients. `classify` and `read_reply` join this
port with the Claude adapter (see docs/integrations/claude-extraction.md).
"""

from typing import Protocol

from finance_ops_agent.domain.reading import TimesheetReading


class CantReadAttachmentError(Exception):
    """The attachment could not be read into a whole form (CANT_READ_ATTACHMENT)."""


class TimesheetReader(Protocol):
    def read_timesheet(self, content: bytes, filename: str, mime_type: str) -> TimesheetReading:
        """Fill in the whole form, or raise CantReadAttachmentError. Never partial."""
        ...
