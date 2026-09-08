"""The port for the agent's own records: items and the audit log.

Whatever the implementation, two rules hold and are checked by the contract
tests in tests/contract/test_store.py:

- One record per consultant per billing period: creating a second item for the
  same consultant, client, and period raises DuplicateItemError.
- Every status change writes an audit row in the same transaction: if either
  the change or the audit row fails, neither happens.
"""

from typing import Protocol

from finance_ops_agent.domain.items import (
    AuditEntry,
    EngagementSnapshot,
    Item,
    OutgoingRecord,
    ReviewRecord,
    TimesheetRecord,
)
from finance_ops_agent.domain.messages import StoredMessage
from finance_ops_agent.domain.money import Hours, Money
from finance_ops_agent.domain.periods import BillingPeriod
from finance_ops_agent.domain.statuses import ItemStatus


class DuplicateItemError(Exception):
    """There is already an item for this consultant, client, and period."""


class Store(Protocol):
    def create_item(
        self,
        consultant: str,
        client: str,
        period: BillingPeriod,
        status: ItemStatus,
        snapshot: EngagementSnapshot,
    ) -> Item:
        """Create the one item for this consultant and period, with an audit row.

        `status` must be one of INITIAL_STATUSES. Raises DuplicateItemError if
        an item for (consultant, client, period) already exists.
        """
        ...

    def get_item(self, item_id: int) -> Item: ...

    def find_item(self, consultant: str, client: str, period: BillingPeriod) -> Item | None: ...

    def list_items(self) -> list[Item]: ...

    def change_status(
        self, item_id: int, new_status: ItemStatus, details: dict[str, object]
    ) -> Item:
        """Apply an allowed status change and its audit row atomically.

        Raises DisallowedStatusChange (and changes nothing) for a change the
        table in the domain does not allow.
        """
        ...

    def audit_entries(self, item_id: int | None = None) -> list[AuditEntry]:
        """The append-only history, oldest first; all of it when item_id is None."""
        ...

    def set_item_amounts(
        self, item_id: int, approved_hours: Hours, invoice_amount: Money, amount_owed: Money
    ) -> Item:
        """Store the hours and both amounts, computed once when the item becomes ready."""
        ...

    # Messages: stored first, marked processed only when handling finished, so a
    # restart picks up exactly where the run stopped and never repeats a message.

    def record_message(self, message: StoredMessage, files: dict[str, bytes]) -> bool:
        """Store a message with its attachment content (by sha256), unprocessed.

        Returns False (storing nothing) if its provider id or internet message
        id was already seen."""
        ...

    def unprocessed_messages(self) -> list[StoredMessage]: ...

    def mark_processed(self, provider_id: str) -> None: ...

    def load_file(self, sha256: str) -> bytes: ...

    # Timesheets

    def timesheet_seen(self, sha256: str) -> bool: ...

    def record_timesheet(self, record: TimesheetRecord) -> None: ...

    def timesheets_for_item(self, item_id: int) -> list[TimesheetRecord]: ...

    # Reviews

    def open_review(self, item_id: int | None, code: str, message: str) -> bool:
        """Open a review for Kevin. Returns False if the same open review
        (item, code, message) already exists, so re-runs never re-ask."""
        ...

    def open_reviews(self) -> list[ReviewRecord]: ...

    # Outgoing: everything to be sent or created is written down before it happens.

    def record_outgoing(
        self, kind: str, idempotency_key: str, item_id: int | None, payload: dict[str, object]
    ) -> bool:
        """Write down an email or accounting write before it happens. Returns
        False if the idempotency key is already recorded (never twice)."""
        ...

    def outgoing_records(self) -> list[OutgoingRecord]: ...

    # Run state (mailbox cursor and friends)

    def get_state(self, key: str) -> str | None: ...

    def set_state(self, key: str, value: str) -> None: ...
