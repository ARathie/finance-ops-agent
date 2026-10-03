"""A fake investigator: makes scripted tool calls through the real toolbox,
then gives a scripted answer.

The calls go through the toolbox the application hands over, exactly as the
Claude adapter's would, so a scenario proves the tools are wired and read-only
without a model. `problems` keeps what it was asked, for tests to read.
"""

from dataclasses import dataclass, field
from typing import Any

from finance_ops_agent.domain.investigation import Investigation, InvestigationResult, ToolCall
from finance_ops_agent.ports.investigator import Toolbox


@dataclass
class FakeInvestigator:
    answer: Investigation | None = None
    calls_to_make: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    answers_seen: list[str] = field(default_factory=list)  # what each tool said

    def investigate(self, problem: str, toolbox: Toolbox) -> InvestigationResult | None:
        self.problems.append(problem)
        calls: list[ToolCall] = []
        for name, arguments in self.calls_to_make:
            text, ok = toolbox.call(name, arguments)
            self.answers_seen.append(text)
            calls.append(ToolCall(name=name, arguments=arguments, ok=ok))
        if self.answer is None:
            return None
        return InvestigationResult(investigation=self.answer, calls=tuple(calls))
