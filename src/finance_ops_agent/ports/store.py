"""The port for the agent's own records: items and the audit log.

Whatever the implementation, two rules hold and are checked by the contract
tests in tests/contract/test_store.py:

- One record per consultant per billing period: creating a second item for the
  same consultant, client, and period raises DuplicateItemError.
- Every status change writes an audit row in the same transaction: if either
  the change or the audit row fails, neither happens.
"""

from typing import Protocol

from finance_ops_agent.domain.items import AuditEntry, EngagementSnapshot, Item
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
