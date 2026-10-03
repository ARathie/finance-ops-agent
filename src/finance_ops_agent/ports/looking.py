"""What diagnosis may touch: the read half of the store, accounting and inbox.

Diagnosis is typed against these narrow views rather than the full ports, so
`mypy --strict` refuses a write from it before any test runs (docs/decisions.md
#60). Every real adapter already satisfies them; nothing has to implement them
separately.
"""

from typing import Protocol

from finance_ops_agent.domain.items import (
    AuditEntry,
    InvoiceRecord,
    Item,
    OutgoingRecord,
    ReviewRecord,
    TimesheetRecord,
)
from finance_ops_agent.domain.messages import InboxEntry, StoredMessage
from finance_ops_agent.ports.accounting import InvoiceLookup


class StoreToLookAt(Protocol):
    def list_items(self) -> list[Item]: ...

    def get_item(self, item_id: int) -> Item: ...

    def invoices_for_item(self, item_id: int) -> list[InvoiceRecord]: ...

    def reviews_for_item(self, item_id: int) -> list[ReviewRecord]: ...

    def open_reviews(self) -> list[ReviewRecord]: ...

    def review_answer(self, review_id: int) -> dict[str, object] | None: ...

    def outgoing_records(self) -> list[OutgoingRecord]: ...

    def audit_entries(self, item_id: int | None = None) -> list[AuditEntry]: ...

    def message_ids_for_item(self, item_id: int) -> list[str]: ...

    def has_message(self, message_id: str) -> bool: ...

    def unprocessed_messages(self) -> list[StoredMessage]: ...

    def get_message(self, message_id: str) -> StoredMessage | None: ...

    def timesheets_for_item(self, item_id: int) -> list[TimesheetRecord]: ...

    def get_state(self, key: str) -> str | None: ...


class AccountingToLookAt(Protocol):
    @property
    def can_look_up_invoices(self) -> bool: ...

    def invoice_lookup(self, external_id: str) -> InvoiceLookup | None: ...

    def invoices_numbered(self, number: str) -> list[InvoiceLookup]: ...


class InboxToLookAt(Protocol):
    def inbox_listing(self, position: str | None) -> list[InboxEntry]: ...
