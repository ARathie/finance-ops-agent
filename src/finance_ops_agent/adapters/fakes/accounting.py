"""An in-memory accounting system for tests.

Like both real adapters, it invoices under the number the application worked
out (docs/decisions.md #27) and keeps its own external id, which is the thing
the accounting system knows the invoice by.
"""

from finance_ops_agent.domain.invoices import Invoice
from finance_ops_agent.ports.accounting import CreatedInvoice


class FakeAccounting:
    def __init__(self) -> None:
        self.invoices: dict[int, CreatedInvoice] = {}
        self.cancelled: list[str] = []
        self.paid: set[str] = set()
        self.asked: list[str] = []
        # Tests set these to make the accounting system misbehave the way a
        # real one does: a refusal, or a connection that needs renewing.
        self.fail_with: Exception | None = None
        self.create_attempts = 0
        self._counter = 0

    def create_invoice(self, invoice: Invoice, item_id: int) -> CreatedInvoice:
        self.create_attempts += 1
        if self.fail_with is not None:
            raise self.fail_with
        existing = self.find_invoice(item_id)
        if existing is not None:
            return existing
        self._counter += 1
        created = CreatedInvoice(
            number=invoice.number, external_id=f"ext-{self._counter}", pdf=b"%PDF-fake"
        )
        self.invoices[item_id] = created
        return created

    def find_invoice(self, item_id: int) -> CreatedInvoice | None:
        created = self.invoices.get(item_id)
        if created is not None and created.external_id in self.cancelled:
            return None
        return created

    def cancel_invoice(self, external_id: str) -> None:
        if self.fail_with is not None:
            raise self.fail_with
        self.cancelled.append(external_id)

    def paid_status(self, external_ids: list[str]) -> dict[str, bool]:
        if self.fail_with is not None:
            raise self.fail_with
        self.asked.extend(external_ids)
        return {external_id: external_id in self.paid for external_id in external_ids}
