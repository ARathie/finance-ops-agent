"""In-memory store for tests. Mirrors the SQLite adapter's behaviour exactly:

the unique item key, allowed status changes only, and the all-or-nothing pairing
of a status change with its audit row (the details are serialised before
anything is mutated, so a bad details dict changes nothing, as a rolled-back
transaction would).
"""

import json
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime

from finance_ops_agent.domain.items import AuditEntry, EngagementSnapshot, Item
from finance_ops_agent.domain.periods import BillingPeriod
from finance_ops_agent.domain.statuses import (
    INITIAL_STATUSES,
    ItemStatus,
    change_status,
)
from finance_ops_agent.ports.store import DuplicateItemError


def _utcnow() -> datetime:
    return datetime.now(UTC)


class FakeStore:
    def __init__(self, now: Callable[[], datetime] = _utcnow) -> None:
        self._now = now
        self._items: dict[int, Item] = {}
        self._audit: list[AuditEntry] = []
        self._next_id = 1

    def create_item(
        self,
        consultant: str,
        client: str,
        period: BillingPeriod,
        status: ItemStatus,
        snapshot: EngagementSnapshot,
    ) -> Item:
        if status not in INITIAL_STATUSES:
            raise ValueError(f"an item cannot start as {status}")
        if self.find_item(consultant, client, period) is not None:
            raise DuplicateItemError(
                f"there is already an item for {consultant} at {client}"
                f" for {period.start} to {period.end}"
            )
        item = Item(
            id=self._next_id,
            consultant=consultant,
            client=client,
            period=period,
            status=status,
            snapshot=snapshot,
        )
        self._next_id += 1
        self._items[item.id] = item
        self._audit.append(
            AuditEntry(
                at=self._now(),
                what="item created",
                item_id=item.id,
                details={"status": status.value},
            )
        )
        return item

    def get_item(self, item_id: int) -> Item:
        return self._items[item_id]

    def find_item(self, consultant: str, client: str, period: BillingPeriod) -> Item | None:
        for item in self._items.values():
            if item.consultant == consultant and item.client == client and item.period == period:
                return item
        return None

    def list_items(self) -> list[Item]:
        return list(self._items.values())

    def change_status(
        self, item_id: int, new_status: ItemStatus, details: dict[str, object]
    ) -> Item:
        item = self._items[item_id]
        change_status(item.status, new_status)
        # Serialise first: if the details can't be written down, nothing changes.
        serialised: dict[str, object] = json.loads(json.dumps(details))
        changed = replace(item, status=new_status)
        self._items[item_id] = changed
        self._audit.append(
            AuditEntry(
                at=self._now(),
                what="status changed",
                item_id=item_id,
                details={"from": item.status.value, "to": new_status.value, **serialised},
            )
        )
        return changed

    def audit_entries(self, item_id: int | None = None) -> list[AuditEntry]:
        if item_id is None:
            return list(self._audit)
        return [entry for entry in self._audit if entry.item_id == item_id]
