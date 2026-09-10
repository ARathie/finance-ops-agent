"""The Claude reader against canned responses - no network, ever.

A stub client plays back what the API would return; refusal and max_tokens
must become CANT_READ_ATTACHMENT, never a partial reading.
"""

import json
from datetime import date
from types import SimpleNamespace
from typing import Any, cast

import anthropic
import httpx2
import pytest

from finance_ops_agent.adapters.claude.reader import ClaudeReader
from finance_ops_agent.domain.reading import (
    ClassifiedKind,
    EmailClassification,
    ReplyAnswer,
    ReplyAnswerKind,
    ReplyReading,
)
from finance_ops_agent.ports.reader import CantReadAttachmentError
from tests.scenarios.conftest import reading

AUG = (date(2026, 8, 1), date(2026, 8, 31))
CSV = b"Date,Hours\n2026-08-03,8.00\n"


def usage(
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_creation_input_tokens: int = 0,
    cache_read_input_tokens: int = 0,
) -> SimpleNamespace:
    """The usage block the Messages API returns alongside a response."""
    return SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_input_tokens=cache_creation_input_tokens,
        cache_read_input_tokens=cache_read_input_tokens,
    )


class StubResponse:
    def __init__(
        self,
        stop_reason: str = "end_turn",
        parsed_output: object = None,
        stop_details: object = None,
        usage: object | None = None,
    ) -> None:
        self.stop_reason = stop_reason
        self.parsed_output = parsed_output
        self.stop_details = stop_details
        if usage is not None:
            self.usage = usage

    def to_json(self) -> str:
        return json.dumps({"stop_reason": self.stop_reason})


class StubClient:
    def __init__(self, outcome: object) -> None:
        self.outcome = outcome
        self.calls: list[dict[str, Any]] = []
        self.messages = SimpleNamespace(parse=self._parse)

    def _parse(self, **kwargs: Any) -> object:
        self.calls.append(kwargs)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def make_reader(outcome: object) -> tuple[ClaudeReader, StubClient, list[bytes]]:
    stub = StubClient(outcome)
    saved: list[bytes] = []
    reader = ClaudeReader(client=cast(anthropic.Anthropic, stub), save_raw=saved.append)
    return reader, stub, saved


def test_a_good_response_returns_the_form_and_saves_the_raw_response() -> None:
    expected = reading(*AUG)
    reader, stub, saved = make_reader(StubResponse(parsed_output=expected))
    result = reader.read_timesheet(CSV, "timesheet.csv", "text/csv")
    assert result == expected
    assert saved  # the raw response was written down for replay and audit
    call = stub.calls[0]
    assert call["model"] == "claude-opus-5"
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "never" in call["system"][0]["text"].lower()  # the read-don't-decide prompt


def test_refusal_is_cant_read_never_partial() -> None:
    reader, _, _ = make_reader(
        StubResponse(
            stop_reason="refusal",
            parsed_output=reading(*AUG),  # even if a partial form came back
            stop_details=SimpleNamespace(explanation="declined to read this"),
        )
    )
    with pytest.raises(CantReadAttachmentError, match="declined to read this"):
        reader.read_timesheet(CSV, "timesheet.csv", "text/csv")


def test_max_tokens_is_cant_read_never_partial() -> None:
    reader, _, _ = make_reader(StubResponse(stop_reason="max_tokens", parsed_output=reading(*AUG)))
    with pytest.raises(CantReadAttachmentError):
        reader.read_timesheet(CSV, "timesheet.csv", "text/csv")


def test_no_parsed_output_is_cant_read() -> None:
    reader, _, _ = make_reader(StubResponse(parsed_output=None))
    with pytest.raises(CantReadAttachmentError):
        reader.read_timesheet(CSV, "timesheet.csv", "text/csv")


def test_bad_request_is_cant_read() -> None:
    error = anthropic.BadRequestError(
        "request too large",
        response=httpx2.Response(400, request=httpx2.Request("POST", "https://api.invalid")),
        body=None,
    )
    reader, _, _ = make_reader(error)
    with pytest.raises(CantReadAttachmentError):
        reader.read_timesheet(CSV, "timesheet.csv", "text/csv")


def test_transient_errors_propagate_for_the_run_to_retry() -> None:
    error = anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.invalid"))
    reader, _, _ = make_reader(error)
    with pytest.raises(anthropic.APIConnectionError):
        reader.read_timesheet(CSV, "timesheet.csv", "text/csv")


def test_unreadable_attachment_never_reaches_the_model() -> None:
    reader, stub, _ = make_reader(StubResponse(parsed_output=reading(*AUG)))
    with pytest.raises(CantReadAttachmentError):
        reader.read_timesheet(b"???", "mystery.bin", "application/octet-stream")
    assert stub.calls == []


def test_classify() -> None:
    expected = EmailClassification(kind=ClassifiedKind.TIMESHEET, reason="hours attached")
    reader, stub, _ = make_reader(StubResponse(parsed_output=expected))
    result = reader.classify(
        "priya@example.com", "August timesheet", "see attached", ["timesheet.pdf"]
    )
    assert result == expected
    assert stub.calls[0]["output_format"] is EmailClassification


def test_read_reply() -> None:
    expected = ReplyReading(
        answers=[
            ReplyAnswer(
                review_code="HOURS_DONT_ADD_UP",
                kind=ReplyAnswerKind.HOURS,
                value="152",
                quote="use 152 hours",
            )
        ]
    )
    reader, stub, _ = make_reader(StubResponse(parsed_output=expected))
    result = reader.read_reply(
        "use 152 hours", [("HOURS_DONT_ADD_UP", "The daily hours don't add up.")]
    )
    assert result == expected
    assert "use 152 hours" in stub.calls[0]["messages"][0]["content"][0]["text"]


def test_token_usage_is_recorded_so_a_live_run_can_be_costed() -> None:
    expected = reading(*AUG)
    reader, _, _ = make_reader(
        StubResponse(
            parsed_output=expected,
            usage=usage(input_tokens=3_000, output_tokens=400, cache_read_input_tokens=1_200),
        )
    )
    reader.read_timesheet(CSV, "timesheet.csv", "text/csv")
    assert reader.usage.requests == 1
    assert reader.usage.input_tokens == 3_000
    assert reader.usage.output_tokens == 400
    assert reader.usage.cache_read_input_tokens == 1_200


def test_usage_accumulates_over_every_call() -> None:
    reader, _, _ = make_reader(
        StubResponse(parsed_output=reading(*AUG), usage=usage(input_tokens=100, output_tokens=10))
    )
    reader.read_timesheet(CSV, "a.csv", "text/csv")
    reader.read_timesheet(CSV, "b.csv", "text/csv")
    assert reader.usage.requests == 2
    assert reader.usage.input_tokens == 200
    assert reader.usage.output_tokens == 20


def test_a_refusal_still_counts_its_tokens_because_it_was_still_billed() -> None:
    reader, _, _ = make_reader(
        StubResponse(
            stop_reason="refusal",
            stop_details=SimpleNamespace(explanation="no"),
            usage=usage(input_tokens=2_000, output_tokens=5),
        )
    )
    with pytest.raises(CantReadAttachmentError):
        reader.read_timesheet(CSV, "timesheet.csv", "text/csv")
    assert reader.usage.requests == 1
    assert reader.usage.input_tokens == 2_000


def test_a_response_without_a_usage_block_is_counted_as_zero_not_an_error() -> None:
    reader, _, _ = make_reader(StubResponse(parsed_output=reading(*AUG)))
    reader.read_timesheet(CSV, "timesheet.csv", "text/csv")
    assert reader.usage.requests == 1
    assert reader.usage.input_tokens == 0
