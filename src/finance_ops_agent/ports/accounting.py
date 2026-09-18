"""The port for the accounting system: manual mode now, QuickBooks Online later.

`find_invoice(item_id)` is the crash-safety hook: before ever creating an
invoice, the agent asks whether one for this item already exists (in QuickBooks
that is the item id kept in the invoice's private note), so a restart never
creates a second one.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from finance_ops_agent.domain.invoices import Invoice


class AccountingFailed(Exception):
    """The accounting system refused or could not do it (becomes QUICKBOOKS_FAILED).

    Defined here rather than in an adapter because the application has to catch
    it, and `application/` never imports an adapter (decision 6). Adapters raise
    their own subclasses of these two.
    """


class AccountingNeedsReconnect(AccountingFailed):
    """The connection is no longer usable (becomes QUICKBOOKS_RECONNECT).

    A subclass, so anything catching AccountingFailed catches this too, and
    anything that wants to tell them apart catches this one first. Manual mode
    raises neither: there is nothing to connect to.
    """


@dataclass(frozen=True)
class EngagementRates:
    """What the accounting system holds about one engagement's two rates.

    `pay_rate_cents` is None where nothing has been put on the product's
    purchase side, which is not the same as nothing being owed: the caller
    keeps what the engagement list says (docs/decisions.md #38).
    """

    bill_rate_cents: int
    pay_rate_cents: int | None
    payee: str


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

    def engagement_rates(self, consultant: str, clients: Sequence[str]) -> EngagementRates | None:
        """What this engagement is billed and paid at, or None where the
        accounting system has nothing to say. Manual mode always says None:
        there is nothing to ask."""
        ...

    def cancel_invoice(self, external_id: str, renamed_to: str | None = None) -> None:
        """Cancel it, renaming it first where the accounting system can.

        `renamed_to` is what the cancelled invoice should be called instead, so
        its real number comes free for the replacement (docs/decisions.md #34).
        QuickBooks will not reuse a number a voided invoice still holds.
        """
        ...

    def paid_status(self, external_ids: list[str]) -> dict[str, bool]: ...
