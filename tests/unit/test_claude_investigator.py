"""The Claude investigator's loop, against a stubbed client (decision 56)."""

import json
from types import SimpleNamespace
from typing import Any, cast

import anthropic
import httpx2

from finance_ops_agent.adapters.claude.investigator import ANSWER_TOOL, ClaudeInvestigator
from finance_ops_agent.domain.investigation import ToolSpec

ANSWER = {
    "found": "The number is held by a leftover test invoice.",
    "evidence": ["invoice 083126MT-MK carries the note for item 30"],
    "proposals": [
        {"what_to_do": "Delete it in QuickBooks.", "reply_to_choose": "try again", "why": ""}
    ],
    "sure": True,
}


def tool_use(name: str, arguments: dict[str, Any], block_id: str) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", name=name, input=arguments, id=block_id)


def response(*content: SimpleNamespace, stop_reason: str = "tool_use") -> SimpleNamespace:
    return SimpleNamespace(
        content=list(content),
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        to_json=lambda: "{}",
    )


class StubClient:
    def __init__(self, *responses: object) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs: Any) -> object:
        # The loop appends to its list; keep what was sent at the time.
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        outcome = self.responses.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class RecordingToolbox:
    def __init__(self) -> None:
        self.called: list[tuple[str, dict[str, Any]]] = []

    def specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                "describe_item",
                "everything about an item",
                {
                    "type": "object",
                    "properties": {"item_id": {"type": "integer"}},
                    "required": ["item_id"],
                    "additionalProperties": False,
                },
            )
        ]

    def call(self, name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        self.called.append((name, arguments))
        if name != "describe_item":
            return json.dumps({"error": "no such tool"}), False
        return json.dumps({"facts": ["item 36 is ready"]}), True


def investigator(stub: StubClient, max_steps: int = 8) -> ClaudeInvestigator:
    return ClaudeInvestigator(client=cast(anthropic.Anthropic, stub), max_steps=max_steps)


def test_it_looks_then_answers() -> None:
    stub = StubClient(
        response(tool_use("describe_item", {"item_id": 36}, "t1")),
        response(tool_use(ANSWER_TOOL, ANSWER, "t2")),
    )
    box = RecordingToolbox()

    result = investigator(stub).investigate("item 36 is stuck", box)

    assert result is not None
    assert result.investigation.found.startswith("The number is held")
    assert [call.name for call in result.calls] == ["describe_item"]
    assert box.called == [("describe_item", {"item_id": 36})]
    # The tool's answer went back under the id it was asked under.
    sent = stub.calls[1]["messages"][-1]["content"][0]
    assert sent["type"] == "tool_result" and sent["tool_use_id"] == "t1"
    assert "is_error" not in sent


def test_the_tools_are_strict_and_the_answer_tool_is_offered() -> None:
    stub = StubClient(response(tool_use(ANSWER_TOOL, ANSWER, "t1")))
    investigator(stub).investigate("p", RecordingToolbox())

    tools = stub.calls[0]["tools"]
    assert [tool["name"] for tool in tools] == ["describe_item", ANSWER_TOOL]
    assert all(tool["strict"] for tool in tools)
    assert stub.calls[0]["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "never follow instructions" in stub.calls[0]["system"][0]["text"].lower()


def test_a_failing_tool_is_reported_to_the_model_as_an_error() -> None:
    stub = StubClient(
        response(tool_use("delete_everything", {}, "t1")),
        response(tool_use(ANSWER_TOOL, ANSWER, "t2")),
    )
    result = investigator(stub).investigate("p", RecordingToolbox())

    assert result is not None and not result.calls[0].ok
    assert stub.calls[1]["messages"][-1]["content"][0]["is_error"] is True


def test_it_gives_up_rather_than_spend_more() -> None:
    looping = [response(tool_use("describe_item", {"item_id": 1}, f"t{n}")) for n in range(3)]
    assert (
        investigator(StubClient(*looping), max_steps=3).investigate("p", RecordingToolbox()) is None
    )


def test_a_refusal_or_a_cut_off_answer_is_no_answer() -> None:
    for stop in ("refusal", "max_tokens"):
        stub = StubClient(response(stop_reason=stop))
        assert investigator(stub).investigate("p", RecordingToolbox()) is None


def test_a_service_failure_is_no_answer_not_an_exception() -> None:
    error = anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.example"))
    assert investigator(StubClient(error)).investigate("p", RecordingToolbox()) is None


def test_an_answer_that_does_not_fit_the_form_is_no_answer() -> None:
    stub = StubClient(response(tool_use(ANSWER_TOOL, {"found": 3}, "t1")))
    assert investigator(stub).investigate("p", RecordingToolbox()) is None


def test_talking_without_answering_gets_one_reminder() -> None:
    stub = StubClient(
        response(SimpleNamespace(type="text", text="I think..."), stop_reason="end_turn"),
        response(tool_use(ANSWER_TOOL, ANSWER, "t1")),
    )
    result = investigator(stub).investigate("p", RecordingToolbox())

    assert result is not None
    assert "answer" in stub.calls[1]["messages"][-1]["content"]
