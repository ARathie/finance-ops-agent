"""Write tracking.xlsx: one row per timesheet item, rewritten after every run.

The sheet is a copy for Kevin's reading and filing; the database is what the
agent works from, so this file is always safe to delete and rewrite.
"""

import io
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font

from finance_ops_agent.domain.tracking import TRACKING_COLUMNS, TrackingRow


def tracking_sheet_bytes(rows: list[TrackingRow]) -> bytes:
    buffer = io.BytesIO()
    _build_workbook(rows).save(buffer)
    return buffer.getvalue()


def write_tracking_sheet(rows: list[TrackingRow], path: Path) -> None:
    _build_workbook(rows).save(path)


def _build_workbook(rows: list[TrackingRow]) -> Workbook:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None  # a new workbook always has one sheet
    sheet.title = "Tracking"
    sheet.append(list(TRACKING_COLUMNS))
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for row in rows:
        sheet.append(list(row.cells()))
    sheet.freeze_panes = "A2"
    return workbook
