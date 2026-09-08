"""One row of the tracking sheet, with the columns from docs/status-tracking.md.

Values are already-formatted text (Kevin's spreadsheet formats: "156.00",
"21,840.00", "2026-08-01 to 2026-08-31"); assembling rows from items is
application code.
"""

from dataclasses import astuple, dataclass

TRACKING_COLUMNS = (
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


@dataclass(frozen=True)
class TrackingRow:
    consultant: str
    client: str
    period: str
    approved_hours: str
    bill_rate: str
    invoice_amount: str
    pay_rate: str
    amount_owed: str
    owed_to: str
    status: str
    timesheet_received: str
    invoice_number: str
    invoice_sent: str
    client_paid: str
    notes: str

    def cells(self) -> tuple[str, ...]:
        cells: tuple[str, ...] = astuple(self)
        return cells
