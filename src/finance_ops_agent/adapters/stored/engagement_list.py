"""The engagement list kept in the agent's own store, not in a file.

The workbook is on its way out: rates and the consultant-client pairing live
in QuickBooks now (decisions 30, 36 and 38), and what is left is Icon's own
operating policy -- billing schedules, the addresses timesheets arrive from,
the names to match on -- which no accounting system has a home for
(docs/decisions.md #40).

What is stored is the workbook exactly as it was read: sheets of rows of cells,
by row number. Nothing downstream changes, because nothing downstream can tell
the difference -- the same parsing, the same checks, the same problems naming
the same sheet and row. Fields leave it one at a time as they move to
QuickBooks, rather than everything moving at once.
"""

import json
from typing import Any

from finance_ops_agent.domain.engagements import RawRow, RawWorkbook
from finance_ops_agent.ports.store import Store

ENGAGEMENT_LIST_KEY = "engagement_list"
SHEETS = ("clients", "consultants", "vendors", "engagements")


class NotImported(Exception):
    """Nothing has been put in the store yet; `fops engagements import` has not run."""


def to_json(workbook: RawWorkbook) -> str:
    return json.dumps(
        {
            sheet: [
                {"row_number": row.row_number, "cells": dict(row.cells)}
                for row in getattr(workbook, sheet)
            ]
            for sheet in SHEETS
        },
        indent=2,
        sort_keys=True,
    )


def from_json(text: str) -> RawWorkbook:
    raw: dict[str, Any] = json.loads(text)
    rows = {
        sheet: [
            RawRow(int(row["row_number"]), {str(k): str(v) for k, v in row["cells"].items()})
            for row in raw.get(sheet, [])
        ]
        for sheet in SHEETS
    }
    return RawWorkbook(
        clients=rows["clients"],
        consultants=rows["consultants"],
        vendors=rows["vendors"],
        engagements=rows["engagements"],
    )


class StoredEngagementList:
    """The EngagementList port, reading what `fops engagements import` wrote."""

    def __init__(self, store: Store) -> None:
        self._store = store

    def load(self) -> RawWorkbook:
        text = self._store.get_state(ENGAGEMENT_LIST_KEY)
        if not text:
            raise NotImported(
                "The engagement list is not in the agent's store yet."
                " Run `fops engagements import` to put it there."
            )
        return from_json(text)


def imported(store: Store) -> bool:
    return bool(store.get_state(ENGAGEMENT_LIST_KEY))


def put(store: Store, workbook: RawWorkbook) -> None:
    store.set_state(ENGAGEMENT_LIST_KEY, to_json(workbook))
