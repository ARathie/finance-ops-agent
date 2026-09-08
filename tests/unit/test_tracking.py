from pathlib import Path

from openpyxl import load_workbook

from finance_ops_agent.adapters.excel.tracking import write_tracking_sheet
from finance_ops_agent.domain.tracking import TRACKING_COLUMNS, TrackingRow


def example_row() -> TrackingRow:
    return TrackingRow(
        consultant="Priya Shah",
        client="Acme Corp",
        period="2026-08-01 to 2026-08-31",
        approved_hours="156.00",
        bill_rate="140.00",
        invoice_amount="21,840.00",
        pay_rate="100.00",
        amount_owed="15,600.00",
        owed_to="Priya Shah (bank transfer), due 2026-09-15",
        status="invoice_sent",
        timesheet_received="2026-09-02",
        invoice_number="1043",
        invoice_sent="2026-09-03",
        client_paid="",
        notes="corrected timesheet received 2026-09-05, used new one",
    )


def test_columns_match_the_docs() -> None:
    assert TRACKING_COLUMNS == (
        "Consultant",
        "Client",
        "Period",
        "Approved hours",
        "Bill rate",
        "Invoice amount",
        "Pay rate",
        "Amount owed",
        "Owed to",
        "Status",
        "Timesheet received",
        "Invoice number",
        "Invoice sent",
        "Client paid",
        "Notes",
    )


def test_writes_the_sheet_with_the_documented_columns(tmp_path: Path) -> None:
    path = tmp_path / "tracking.xlsx"
    write_tracking_sheet([example_row()], path)

    sheet = load_workbook(path)["Tracking"]
    rows = [[cell if cell is not None else "" for cell in row] for row in sheet.values]
    assert rows[0] == list(TRACKING_COLUMNS)
    assert rows[1] == list(example_row().cells())
    assert len(rows) == 2


def test_rewriting_replaces_the_file(tmp_path: Path) -> None:
    path = tmp_path / "tracking.xlsx"
    write_tracking_sheet([example_row()], path)
    write_tracking_sheet([], path)
    sheet = load_workbook(path)["Tracking"]
    assert len(list(sheet.values)) == 1  # just the header
