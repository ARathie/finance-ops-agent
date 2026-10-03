"""The investigator's tools: the read-only diagnosis, described for a model.

Every tool here calls a function in `application/diagnosis.py` or reads the
store through the read-only view, so the whole list is read-only by
construction (decision 60): it holds nothing that could change a record, an
invoice or the mailbox. Tools that act, when they come, will be a separate list
with its own decision, never added to this one (decision 61).

Answers are JSON text. A tool that fails returns its error as text instead of
raising, so one bad call cannot end an investigation.
"""

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

from finance_ops_agent.application.diagnosis import (
    Finding,
    Looking,
    check_inbox,
    check_items,
    check_recorded_invoices,
    describe_item,
    explain_invoice_number,
)
from finance_ops_agent.domain.emails import format_period
from finance_ops_agent.domain.investigation import ToolSpec, without_money
from finance_ops_agent.domain.statuses import ItemStatus
from finance_ops_agent.ports.accounting import AccountingFailed

MAX_ANSWER_CHARS = 20_000  # a tool answer longer than this is cut, and says so

_NO_ARGUMENTS: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "required": [],
    "additionalProperties": False,
}


def _finding(finding: Finding) -> dict[str, Any]:
    shown = asdict(finding)
    shown["kind"] = finding.kind.value
    return shown


def _findings(findings: list[Finding]) -> dict[str, Any]:
    return {"findings": [_finding(finding) for finding in findings]}


@dataclass(frozen=True)
class _Tool:
    spec: ToolSpec
    run: Callable[[Looking, dict[str, Any]], dict[str, Any]]


def _describe_item(looking: Looking, arguments: dict[str, Any]) -> dict[str, Any]:
    item_id = int(arguments["item_id"])
    if item_id not in {item.id for item in looking.store.list_items()}:
        return {"error": f"there is no item {item_id}"}
    report = describe_item(looking, item_id)
    return {
        "item_id": report.item.id,
        "facts": report.facts,
        **_findings(report.findings),
    }


def _explain_number(looking: Looking, arguments: dict[str, Any]) -> dict[str, Any]:
    item_id = arguments.get("item_id")
    number = str(arguments["number"]).strip()
    return _findings(
        explain_invoice_number(looking, number, None if item_id is None else int(item_id))
    )


def _list_items(looking: Looking, arguments: dict[str, Any]) -> dict[str, Any]:
    wanted = arguments.get("status")
    if wanted in (None, "any"):
        wanted = None
    elif wanted not in {status.value for status in ItemStatus}:
        return {"error": f"no such status {wanted!r}"}
    return {
        "items": [
            {
                "item_id": item.id,
                "consultant": item.consultant,
                "client": item.client,
                "period": format_period(item.period),
                "status": item.status.value,
            }
            for item in looking.store.list_items()
            if wanted is None or item.status.value == wanted
        ]
    }


def _open_reviews(looking: Looking, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "reviews": [
            {"item_id": review.item_id, "code": review.code, "message": review.message}
            for review in looking.store.open_reviews()
        ]
    }


def _check_items(looking: Looking, arguments: dict[str, Any]) -> dict[str, Any]:
    assert looking.workbook is not None  # only offered when there is one
    return _findings(check_items(looking, looking.workbook))


_TOOLS: tuple[_Tool, ...] = (
    _Tool(
        ToolSpec(
            "describe_item",
            "Everything the agent knows about one timesheet item: who, which client,"
            " which period, its status, its invoices (number, QuickBooks id, status),"
            " every review raised on it and its answer, every email written about it,"
            " its history, and any problem with its invoices or invoice number.",
            {
                "type": "object",
                "properties": {"item_id": {"type": "integer"}},
                "required": ["item_id"],
                "additionalProperties": False,
            },
        ),
        _describe_item,
    ),
    _Tool(
        ToolSpec(
            "explain_invoice_number",
            "Which invoice in QuickBooks holds an invoice number, and whose work it is:"
            " the agent's own invoice for a current item, one for an item that was"
            " removed (forgotten), or one someone made by hand. Give item_id for the"
            " item that wants the number, or null.",
            {
                "type": "object",
                "properties": {
                    "number": {"type": "string"},
                    "item_id": {"type": ["integer", "null"]},
                },
                "required": ["number", "item_id"],
                "additionalProperties": False,
            },
        ),
        _explain_number,
    ),
    _Tool(
        ToolSpec(
            "check_recorded_invoices",
            "Every invoice in the agent's records, looked up in QuickBooks: ones"
            " QuickBooks does not have (deleted, or made in another company such as"
            " the sandbox), ones renumbered there, and ones that belong to another item.",
            _NO_ARGUMENTS,
        ),
        lambda looking, _: _findings(check_recorded_invoices(looking)),
    ),
    _Tool(
        ToolSpec(
            "list_items",
            "Every timesheet item the agent holds, with its id, consultant, client,"
            ' period and status. Give a status to see only those, or "any" for all.',
            {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["any", *[status.value for status in ItemStatus]],
                    }
                },
                "required": ["status"],
                "additionalProperties": False,
            },
        ),
        _list_items,
    ),
    _Tool(
        ToolSpec(
            "list_open_reviews",
            "Every question still waiting on Kevin: the item, the review code, and"
            " exactly what he was told.",
            _NO_ARGUMENTS,
        ),
        _open_reviews,
    ),
)

_INBOX_TOOL = _Tool(
    ToolSpec(
        "check_inbox",
        "Every email in the agent's inbox and whether the next run will read it:"
        " already handled (and for which item), dated before the mail start date,"
        " already read past, or new.",
        _NO_ARGUMENTS,
    ),
    lambda looking, _: _findings(check_inbox(looking)),
)

_ITEMS_TOOL = _Tool(
    ToolSpec(
        "check_items",
        "Items that match no current engagement (left from testing, or from before a"
        " client was renamed), and engagements waiting on suspiciously many months of"
        " timesheets because their start date is early.",
        _NO_ARGUMENTS,
    ),
    _check_items,
)


class ReadOnlyToolbox:
    """The read-only tools, bound to what they may look at."""

    def __init__(self, looking: Looking) -> None:
        self._looking = looking
        tools = list(_TOOLS)
        if looking.inbox is not None:
            tools.append(_INBOX_TOOL)
        if looking.workbook is not None:
            tools.append(_ITEMS_TOOL)
        self._tools = {tool.spec.name: tool for tool in tools}

    def specs(self) -> list[ToolSpec]:
        return [tool.spec for tool in self._tools.values()]

    def call(self, name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        tool = self._tools.get(name)
        if tool is None:
            return json.dumps({"error": f"there is no tool called {name}"}), False
        try:
            answer = tool.run(self._looking, arguments)
        except AccountingFailed as error:
            return json.dumps({"error": f"QuickBooks could not be asked: {error}"}), False
        except (KeyError, ValueError, TypeError) as error:
            return json.dumps({"error": f"bad arguments for {name}: {error}"}), False
        text = without_money(json.dumps(answer, default=str))
        if len(text) > MAX_ANSWER_CHARS:
            text = text[:MAX_ANSWER_CHARS] + ' ... (cut: the answer was too long)"'
        return text, "error" not in answer
