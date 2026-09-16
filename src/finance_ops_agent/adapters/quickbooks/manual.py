"""Manual QuickBooks mode (docs/decisions.md #11): the agent numbers the
invoice and renders the PDF; Kevin types it into QuickBooks Desktop himself.

The number itself is Kevin's format and is worked out by the application
before either adapter is asked to create anything (docs/decisions.md #27), so
an invoice reads `083126MT-PS` whether it was made here or in QuickBooks
Online. Creating is idempotent by item, so a restart never numbers the same
work twice - here by looking at the invoice rows the application wrote, which
is the same question QuickBooksOnline answers by reading the private note.
"""

from finance_ops_agent.domain.invoices import Invoice
from finance_ops_agent.ports.accounting import CreatedInvoice
from finance_ops_agent.ports.pdf import PdfRenderer
from finance_ops_agent.ports.store import Store


class ManualQuickBooks:
    def __init__(self, store: Store, renderer: PdfRenderer) -> None:
        self._store = store
        self._renderer = renderer

    def create_invoice(self, invoice: Invoice, item_id: int) -> CreatedInvoice:
        existing = self.find_invoice(item_id)
        if existing is not None:
            return existing
        pdf = self._renderer.invoice_pdf(invoice)
        return CreatedInvoice(number=invoice.number, external_id=invoice.number, pdf=pdf)

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
