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

    ref: str  # the accounting system's id for the engagement, stable across renames
    bill_rate_cents: int
    pay_rate_cents: int | None
    payee: str
    payee_ref: str = ""  # the payee's own id, to look up their contact and terms


@dataclass(frozen=True)
class AccountingEngagement:
    """One engagement the accounting system holds, as it holds it.

    In QuickBooks that is an active product under a category: the product is
    the consultant and the category is the client (docs/decisions.md #36).
    Enumerating them is what lets the accounting system say which engagements
    are live, rather than the engagement list's own `Active` column
    (docs/decisions.md #42).

    `bill_rate_cents` is None where the product has no rate on it. That is an
    engagement that cannot be invoiced, and it is listed rather than dropped:
    one that quietly vanished would look exactly like one that had finished.
    """

    ref: str  # the accounting system's id, stable across renames
    consultant: str
    client: str
    bill_rate_cents: int | None
    pay_rate_cents: int | None
    payee: str


@dataclass(frozen=True)
class EngagementListing:
    """What the accounting system holds, and what it looked at to find it.

    The counts are not decoration. "No engagements" and "no products at all"
    call for completely different things to be done about them, and a listing
    that reported only the first left someone guessing which they had.
    """

    live: list[AccountingEngagement]
    products_seen: int = 0
    categories_seen: int = 0


@dataclass(frozen=True)
class AccountingParty:
    """A client or a payee, beyond what an engagement says about them.

    Read so that what QuickBooks holds can be compared with the engagement list
    before anything depends on it, the way the purchase side was in
    docs/decisions.md #37. `None` for the terms means QuickBooks has nothing
    there, which is not the same as nothing being due.
    """

    ref: str
    name: str
    email: str
    payment_terms_days: int | None
    # Why there are no terms, where there are none. "The company has no such
    # term" and "this record does not name one" need different fixes.
    terms_note: str = ""


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

    def engagements(self) -> EngagementListing:
        """Every engagement the accounting system knows is live.

        Empty means "nothing to say", not "nothing is live": manual mode has
        no accounting system to enumerate, and a company whose products have
        not been filled in yet answers the same way. The caller keeps to the
        engagement list in both cases rather than concluding that Icon has
        stopped working.
        """
        ...

    def customer(self, name: str) -> AccountingParty | None:
        """The client as the accounting system holds it, or None where it has
        nothing to say. Manual mode always says None."""
        ...

    def payee(self, ref: str) -> AccountingParty | None:
        """Who Icon pays for an engagement -- the vendor on its product's
        purchase side -- by the id `EngagementRates.payee_ref` carries."""
        ...

    def cancel_invoice(self, external_id: str, renamed_to: str | None = None) -> None:
        """Cancel it, renaming it first where the accounting system can.

        `renamed_to` is what the cancelled invoice should be called instead, so
        its real number comes free for the replacement (docs/decisions.md #34).
        QuickBooks will not reuse a number a voided invoice still holds.
        """
        ...

    def paid_status(self, external_ids: list[str]) -> dict[str, bool]: ...
