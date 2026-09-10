"""The port for reading timesheets (Claude, in PR 6; a scripted fake until then).

The reader only fills in the TimesheetReading form. It never sums hours, never
sees a rate, and never picks recipients. `classify` and `read_reply` join this
port with the Claude adapter (see docs/integrations/claude-extraction.md).
"""

from typing import Protocol

from pydantic import BaseModel, ConfigDict

from finance_ops_agent.domain.reading import ReadingHints, ReplyReading, TimesheetReading


class CantReadAttachmentError(Exception):
    """The attachment could not be read into a whole form (CANT_READ_ATTACHMENT)."""


class TokenUsage(BaseModel):
    """What model calls cost in tokens. Tokens only - never money.

    Turning tokens into money needs prices, which change and are not the
    reader's business; `application/eval_runner.py` does that arithmetic.
    Usage is counted even for a call that ends in a refusal or `max_tokens`,
    because those are billed too.
    """

    model_config = ConfigDict(frozen=True)

    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            requests=self.requests + other.requests,
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_creation_input_tokens=(
                self.cache_creation_input_tokens + other.cache_creation_input_tokens
            ),
            cache_read_input_tokens=self.cache_read_input_tokens + other.cache_read_input_tokens,
        )

    @property
    def total_input_tokens(self) -> int:
        """Every input token, however it was billed."""
        return self.input_tokens + self.cache_creation_input_tokens + self.cache_read_input_tokens


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

    def read_reply(self, reply_text: str, questions: list[tuple[str, str]]) -> ReplyReading:
        """Kevin's short reply, read into typed answers per review reason asked."""
        ...
