"""A fake timesheet reader, scripted per attachment filename."""

from finance_ops_agent.domain.reading import ReadingHints, TimesheetReading
from finance_ops_agent.ports.reader import CantReadAttachmentError


class FakeReader:
    model_name = "fake"
    prompt_version = "0"

    def __init__(self, readings: dict[str, TimesheetReading]) -> None:
        self.readings = readings

    def read_timesheet(
        self,
        content: bytes,
        filename: str,
        mime_type: str,
        hints: ReadingHints | None = None,
    ) -> TimesheetReading:
        try:
            return self.readings[filename]
        except KeyError as error:
            raise CantReadAttachmentError(f"no scripted reading for {filename}") from error
