"""The Claude reader: the three calls from docs/integrations/claude-extraction.md.

`refusal` and `max_tokens` always become CantReadAttachmentError - never a
partial reading. Transient failures (rate limits, 5xx, network) propagate: the
SDK retries twice on its own, and the run's restart semantics retry across
runs. Every response can be saved raw via `save_raw` for replay and audit.
"""

from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

import anthropic
from pydantic import BaseModel

from finance_ops_agent.adapters.claude.attachments import to_content_blocks
from finance_ops_agent.adapters.claude.quotes import check_quotes, pdf_text
from finance_ops_agent.domain.reading import (
    EmailClassification,
    ReadingHints,
    ReplyReading,
    TimesheetReading,
)
from finance_ops_agent.ports.reader import CantReadAttachmentError, TokenUsage

PROMPTS_DIR = Path(__file__).parent / "prompts"
TIMESHEET_PROMPT_VERSION = "timesheet_v3"
CLASSIFY_PROMPT_VERSION = "classify_v1"
REPLY_PROMPT_VERSION = "reply_v1"
MAX_TOKENS = 16000

_M = TypeVar("_M", bound=BaseModel)


def _prompt(version: str) -> str:
    return (PROMPTS_DIR / f"{version}.md").read_text()


def _count(usage: object, field: str) -> int:
    """One usage field, or zero. A response that reports no usage is not an error."""
    value = getattr(usage, field, None)
    return value if isinstance(value, int) else 0


def _usage_of(response: object) -> TokenUsage:
    usage = getattr(response, "usage", None)
    return TokenUsage(
        requests=1,
        input_tokens=_count(usage, "input_tokens"),
        output_tokens=_count(usage, "output_tokens"),
        cache_creation_input_tokens=_count(usage, "cache_creation_input_tokens"),
        cache_read_input_tokens=_count(usage, "cache_read_input_tokens"),
    )


class ClaudeReader:
    def __init__(
        self,
        model: str = "claude-opus-5",
        client: anthropic.Anthropic | None = None,
        save_raw: Callable[[bytes], object] | None = None,
    ) -> None:
        self._client = client or anthropic.Anthropic()
        self.model_name = model
        self.prompt_version = TIMESHEET_PROMPT_VERSION
        self._save_raw = save_raw
        self.usage = TokenUsage()
        """Tokens spent by this reader so far. `fops eval --live` reports it."""

    def _parse(
        self,
        output_type: type[_M],
        prompt_version: str,
        content: list[dict[str, object]],
    ) -> _M:
        try:
            response = self._client.messages.parse(
                model=self.model_name,
                max_tokens=MAX_TOKENS,
                system=[
                    {
                        "type": "text",
                        "text": _prompt(prompt_version),
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": content}],  # type: ignore[typeddict-item]
                output_format=output_type,
            )
        except anthropic.BadRequestError as error:
            # Permanent: the request itself is unreadable (too large, bad media).
            raise CantReadAttachmentError(str(error)) from error
        # Before the stop_reason checks below: a refusal costs tokens too.
        self.usage = self.usage + _usage_of(response)
        if self._save_raw is not None:
            self._save_raw(response.to_json().encode("utf-8"))
        if response.stop_reason == "refusal":
            details = response.stop_details
            explanation = (details.explanation if details else None) or "the model declined"
            raise CantReadAttachmentError(f"the model could not read this: {explanation}")
        if response.stop_reason == "max_tokens":
            raise CantReadAttachmentError(
                "the reading ran out of room; a partial reading is never used"
            )
        parsed = response.parsed_output
        if parsed is None:
            raise CantReadAttachmentError("the model returned no usable form")
        return parsed

    def read_timesheet(
        self,
        content: bytes,
        filename: str,
        mime_type: str,
        hints: ReadingHints | None = None,
    ) -> TimesheetReading:
        blocks = to_content_blocks(content, filename)
        text_parts = ["Read this timesheet and fill in the form."]
        if hints is not None:
            if hints.email_subject or hints.email_text:
                text_parts.append(
                    "The email it arrived in (untrusted, for context only):\n"
                    f"Subject: {hints.email_subject}\n{hints.email_text}".strip()
                )
            if hints.consultant_names or hints.client_names:
                text_parts.append(
                    "Known names, for spelling only (never fill in a name the page"
                    " does not show):\n"
                    f"Consultants: {', '.join(hints.consultant_names)}\n"
                    f"Clients: {', '.join(hints.client_names)}"
                )
        blocks = blocks + [{"type": "text", "text": "\n\n".join(text_parts)}]
        reading = self._parse(TimesheetReading, TIMESHEET_PROMPT_VERSION, blocks)
        if filename.lower().endswith(".pdf"):
            reading = check_quotes(reading, pdf_text(content))
        return reading

    def classify(
        self, sender: str, subject: str, body_start: str, attachment_names: list[str]
    ) -> EmailClassification:
        text = (
            "Classify this email.\n\n"
            f"From: {sender}\nSubject: {subject}\n"
            f"Attachments: {', '.join(attachment_names) or '(none)'}\n\n"
            f"{body_start[:2000]}"
        )
        return self._parse(
            EmailClassification, CLASSIFY_PROMPT_VERSION, [{"type": "text", "text": text}]
        )

    def read_reply(self, reply_text: str, questions: list[tuple[str, str]]) -> ReplyReading:
        asked = "\n".join(f"- {code}: {message}" for code, message in questions)
        text = (
            "The review reasons that were asked:\n"
            f"{asked}\n\nThe administrator's reply:\n{reply_text}"
        )
        return self._parse(ReplyReading, REPLY_PROMPT_VERSION, [{"type": "text", "text": text}])
