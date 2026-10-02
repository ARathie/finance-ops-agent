"""A fake timesheet reader, scripted per attachment filename, and scripted
replies keyed by the reply text."""

from finance_ops_agent.domain.reading import (
    ReadingHints,
    ReplyAnswer,
    ReplyAnswerKind,
    ReplyContext,
    ReplyReading,
    TimesheetReading,
)
from finance_ops_agent.ports.reader import CantReadAttachmentError


class FakeReader:
    model_name = "fake"
    prompt_version = "0"

    def __init__(
        self,
        readings: dict[str, TimesheetReading],
        replies: dict[str, ReplyReading] | None = None,
    ) -> None:
        self.readings = readings
        self.replies = replies or {}
        self.last_reply_context: ReplyContext | None = None

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

    def read_reply(
        self,
        reply_text: str,
        questions: list[tuple[str, str]],
        context: ReplyContext | None = None,
    ) -> ReplyReading:
        self.last_reply_context = context
        scripted = self.replies.get(reply_text.strip())
        if scripted is not None:
            return scripted
        # Unscripted replies read as unclear, which makes the agent ask again.
        first_code = questions[0][0] if questions else ""
        return ReplyReading(
            answers=[ReplyAnswer(review_code=first_code, kind=ReplyAnswerKind.UNCLEAR)]
        )
