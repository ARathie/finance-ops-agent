"""The port for the accounting system: manual mode now, QuickBooks Online later.

`find_invoice(item_id)` is the crash-safety hook: before ever creating an
invoice, the agent asks whether one for this item already exists (in QuickBooks
that is the item id kept in the invoice's private note), so a restart never
creates a second one.
"""

from dataclasses import dataclass
from typing import Protocol

from finance_ops_agent.domain.invoices import Invoice


@dataclass(frozen=True)
class CreatedInvoice:
    number: str
    external_id: str
    pdf: bytes


class AccountingSystem(Protocol):
    def create_invoice(self, invoice: Invoice, item_id: int) -> CreatedInvoice:
        """Create the invoice under the number it already carries, and return
        it with its rendered PDF. The number is Kevin's and is worked out by
        the application before either adapter is asked (docs/decisions.md #27).
        The returned total must equal the invoice's exactly, and the returned
        number must be the one that was asked for; adapters verify and refuse
        otherwise."""
        ...

    def find_invoice(self, item_id: int) -> CreatedInvoice | None: ...

    def cancel_invoice(self, external_id: str) -> None: ...

    def paid_status(self, external_ids: list[str]) -> dict[str, bool]: ...
