"""Manual QuickBooks mode (docs/decisions.md #11): the agent numbers the
invoice and renders the PDF; Kevin types it into QuickBooks Desktop himself.

Numbering continues one counter kept in the agent's own state
(ICON-<year>-<number>, starting from the seed Kevin chooses); creating is
idempotent by item, so a restart never numbers the same work twice.
"""

from dataclasses import replace
from datetime import date

from finance_ops_agent.domain.invoices import Invoice
from finance_ops_agent.domain.items import InvoiceRecord
from finance_ops_agent.ports.accounting import CreatedInvoice
from finance_ops_agent.ports.pdf import PdfRenderer
from finance_ops_agent.ports.store import Store

COUNTER_KEY = "invoice_counter"


class ManualQuickBooks:
    def __init__(self, store: Store, renderer: PdfRenderer, today: date) -> None:
        self._store = store
        self._renderer = renderer
        self._today = today

    def _next_number(self) -> str:
        counter = int(self._store.get_state(COUNTER_KEY) or "1000") + 1
        self._store.set_state(COUNTER_KEY, str(counter))
        return f"ICON-{self._today.year}-{counter}"

    def create_invoice(self, invoice: Invoice, item_id: int) -> CreatedInvoice:
        existing = self.find_invoice(item_id)
        if existing is not None:
            return existing
        numbered = replace(invoice, number=self._next_number())
        pdf = self._renderer.invoice_pdf(numbered)
        pdf_sha = self._store.save_file(pdf)
        self._store.record_invoice(
            InvoiceRecord(
                id=0,
                item_id=item_id,
                number=numbered.number,
                external_id=numbered.number,
                amount_cents=numbered.total.cents,
                issue_date=numbered.issue_date,
                due_date=numbered.due_date,
                pdf_sha256=pdf_sha,
                status="created",
                replaces_number=invoice.replaces_number,
            )
        )
        return CreatedInvoice(number=numbered.number, external_id=numbered.number, pdf=pdf)

    def find_invoice(self, item_id: int) -> CreatedInvoice | None:
        live = [
            record
            for record in self._store.invoices_for_item(item_id)
            if record.status != "cancelled"
        ]
        if not live:
            return None
        record = live[-1]
        pdf = self._store.load_file(record.pdf_sha256) if record.pdf_sha256 else b""
        return CreatedInvoice(number=record.number, external_id=record.external_id, pdf=pdf)

    def cancel_invoice(self, external_id: str) -> None:
        self._store.set_invoice_status(external_id, "cancelled")

    def paid_status(self, external_ids: list[str]) -> dict[str, bool]:
        # Manual mode cannot see payments; QuickBooks Online (PR 9) can.
        return dict.fromkeys(external_ids, False)
