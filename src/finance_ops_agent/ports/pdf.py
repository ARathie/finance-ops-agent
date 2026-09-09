"""The port for rendering the invoice PDF."""

from typing import Protocol

from finance_ops_agent.domain.invoices import Invoice


class PdfRenderer(Protocol):
    def invoice_pdf(self, invoice: Invoice) -> bytes: ...
