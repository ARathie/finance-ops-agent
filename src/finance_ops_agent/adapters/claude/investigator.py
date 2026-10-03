"""The Claude investigator: a short tool-use loop over the tools it is lent.

A hand-written loop rather than the SDK's beta tool runner, because the loop
is where the guarantees live (docs/decisions.md #61):

- a fixed number of steps, after which it gives up rather than spends more;
- every tool call recorded, with whether it worked;
- an answer that is malformed (tags instead of plain fields, no ways out)
  is refused back to the model to try again, never passed on;
- any refusal, truncation or service failure ends it with None, never an
  exception, so a stuck investigation can only leave the review email as it
  would have been;
- the answer arrives through an `answer` tool with a strict schema, and is
  validated again here before anything uses it.

The tools are whatever the application handed over; this adapter has nothing
of its own to reach.
"""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import anthropic
from pydantic import ValidationError

from finance_ops_agent import logs
from finance_ops_agent.adapters.claude.reader import _usage_of
from finance_ops_agent.domain.investigation import (
    Investigation,
    InvestigationResult,
    ToolCall,
    problems_with_answer,
)
from finance_ops_agent.ports.investigator import Toolbox
from finance_ops_agent.ports.reader import TokenUsage

PROMPTS_DIR = Path(__file__).parent / "prompts"
INVESTIGATE_PROMPT_VERSION = "investigate_v2"
MAX_TOKENS = 16000
MAX_STEPS = 8  # model turns; each may call several tools
ANSWER_TOOL = "answer"

_ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "found": {"type": "string"},
        "evidence": {"type": "array", "items": {"type": "string"}},
        "proposals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "what_to_do": {"type": "string"},
                    "reply_to_choose": {"type": "string"},
                    "why": {"type": "string"},
                },
                "required": ["what_to_do", "reply_to_choose", "why"],
                "additionalProperties": False,
            },
        },
        "sure": {"type": "boolean"},
    },
    "required": ["found", "evidence", "proposals", "sure"],
    "additionalProperties": False,
}


def _prompt() -> str:
    return (PROMPTS_DIR / f"{INVESTIGATE_PROMPT_VERSION}.md").read_text()


class ClaudeInvestigator:
    def __init__(
        self,
        model: str = "claude-opus-5",
        client: anthropic.Anthropic | None = None,
        max_steps: int = MAX_STEPS,
        save_raw: Callable[[bytes], object] | None = None,
    ) -> None:
        self._client = client or anthropic.Anthropic()
        self.model_name = model
        self.prompt_version = INVESTIGATE_PROMPT_VERSION
        self._max_steps = max_steps
        self._save_raw = save_raw
        self.usage = TokenUsage()

    def _tools(self, toolbox: Toolbox) -> list[dict[str, Any]]:
        offered = [
            {
                "name": spec.name,
                "description": spec.description,
                "input_schema": spec.input_schema,
                "strict": True,
            }
            for spec in toolbox.specs()
        ]
        offered.append(
            {
                "name": ANSWER_TOOL,
                "description": "Give your answer. Call this once, last, when you are done.",
                "input_schema": _ANSWER_SCHEMA,
                "strict": True,
            }
        )
        return offered

    def investigate(self, problem: str, toolbox: Toolbox) -> InvestigationResult | None:
        tools = self._tools(toolbox)
        messages: list[dict[str, Any]] = [{"role": "user", "content": problem}]
        calls: list[ToolCall] = []
        for _ in range(self._max_steps):
            try:
                response = self._client.messages.create(
                    model=self.model_name,
                    max_tokens=MAX_TOKENS,
                    system=[
                        {
                            "type": "text",
                            "text": _prompt(),
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                    tools=tools,  # type: ignore[arg-type]
                    messages=messages,  # type: ignore[arg-type]
                )
            except anthropic.APIError as error:
                logs.log("investigation failed", said=str(error)[:300])
                return None
            self.usage = self.usage + _usage_of(response)
            if self._save_raw is not None:
                self._save_raw(response.to_json().encode("utf-8"))
            if response.stop_reason in ("refusal", "max_tokens"):
                logs.log("investigation stopped", stop_reason=response.stop_reason)
                return None
            uses = [block for block in response.content if block.type == "tool_use"]
            refused = ""
            answer = next((block for block in uses if block.name == ANSWER_TOOL), None)
            if answer is not None:
                result, refused = self._answer(answer.input, calls)
                if result is not None:
                    return result
                # Not usable as it is: say why and let it answer again, as
                # another step. Kevin never sees a malformed answer.
                logs.log("investigation answer refused", said=refused[:300])
            if not uses:
                # It stopped talking without answering: one reminder, counted
                # as a step like any other.
                messages.append({"role": "assistant", "content": response.content})
                messages.append(
                    {"role": "user", "content": f"Please finish by calling `{ANSWER_TOOL}`."}
                )
                continue
            messages.append({"role": "assistant", "content": response.content})
            results: list[dict[str, Any]] = []
            for block in uses:
                if block.name == ANSWER_TOOL:
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": f"Not accepted: {refused}. Call `{ANSWER_TOOL}` again"
                            " with plain sentences in every field.",
                            "is_error": True,
                        }
                    )
                    continue
                arguments = dict(block.input) if isinstance(block.input, dict) else {}
                text, ok = toolbox.call(block.name, arguments)
                calls.append(ToolCall(name=block.name, arguments=arguments, ok=ok))
                logs.log("investigation looked", tool=block.name, arguments=arguments, ok=ok)
                result_block: dict[str, Any] = {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": text,
                }
                if not ok:
                    result_block["is_error"] = True
                results.append(result_block)
            # Every result in one message, so parallel calls stay parallel.
            messages.append({"role": "user", "content": results})
        logs.log("investigation ran out of steps", steps=self._max_steps)
        return None

    def _answer(self, raw: object, calls: list[ToolCall]) -> tuple[InvestigationResult | None, str]:
        """The answer, or None and why it cannot be used."""
        try:
            given = raw if isinstance(raw, dict) else json.loads(str(raw))
            investigation = Investigation.model_validate(given)
        except (ValidationError, ValueError) as error:
            return None, f"it does not fit the form ({str(error)[:200]})"
        problems = problems_with_answer(investigation)
        if problems:
            return None, "; ".join(problems)
        return InvestigationResult(investigation=investigation, calls=tuple(calls)), ""
