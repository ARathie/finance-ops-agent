"""The port for the investigator: looks into a stuck item with tools it is lent.

The tools come from the application (`application/agent_tools.py`), not from
the adapter, so the adapter cannot reach anything it was not handed. Today the
toolbox it is handed holds read-only tools only (docs/decisions.md #61).
"""

from typing import Any, Protocol

from finance_ops_agent.domain.investigation import InvestigationResult, ToolSpec


class Toolbox(Protocol):
    def specs(self) -> list[ToolSpec]:
        """Every tool on offer, described for the model."""
        ...

    def call(self, name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        """Run one tool. Returns its answer as text (JSON) and whether it
        worked; a tool that fails says why instead of raising."""
        ...


class Investigator(Protocol):
    def investigate(self, problem: str, toolbox: Toolbox) -> InvestigationResult | None:
        """Look into `problem` with the toolbox and say what was found and what
        Kevin could do. None when it could not reach an answer (out of steps,
        refused, or the service failed): the review goes out as it would have
        anyway, so an investigation can only ever add to what Kevin is told."""
        ...
