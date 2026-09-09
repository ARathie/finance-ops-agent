"""An in-memory accounting system for tests."""

from dataclasses import replace

from finance_ops_agent.domain.invoices import Invoice
from finance_ops_agent.ports.accounting import CreatedInvoice


class FakeAccounting:
    def __init__(self) -> None:
        self.invoices: dict[int, CreatedInvoice] = {}
        self.cancelled: list[str] = []
        self.paid: set[str] = set()
        self._counter = 0

    def create_invoice(self, invoice: Invoice, item_id: int) -> CreatedInvoice:
        existing = self.find_invoice(item_id)
        if existing is not None:
            return existing
        self._counter += 1
        numbered = replace(invoice, number=f"FAKE-{self._counter}")
        created = CreatedInvoice(
            number=numbered.number, external_id=f"ext-{self._counter}", pdf=b"%PDF-fake"
        )
        self.invoices[item_id] = created
        return created

    def find_invoice(self, item_id: int) -> CreatedInvoice | None:
        created = self.invoices.get(item_id)
        if created is not None and created.external_id in self.cancelled:
            return None
        return created

    def cancel_invoice(self, external_id: str) -> None:
        self.cancelled.append(external_id)

    def paid_status(self, external_ids: list[str]) -> dict[str, bool]:
        return {external_id: external_id in self.paid for external_id in external_ids}
