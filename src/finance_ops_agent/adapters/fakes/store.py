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
        self._messages: dict[str, StoredMessage] = {}
        self._internet_ids: set[str] = set()
        self._files: dict[str, bytes] = {}
        self._timesheets: list[TimesheetRecord] = []
        self._reviews: list[ReviewRecord] = []
        self._next_review_id = 1
        self._outgoing: dict[str, OutgoingRecord] = {}
        self._state: dict[str, str] = {}

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

    def set_item_amounts(
        self, item_id: int, approved_hours: Hours, invoice_amount: Money, amount_owed: Money
    ) -> Item:
        changed = replace(
            self._items[item_id],
            approved_hours=approved_hours,
            invoice_amount=invoice_amount,
            amount_owed=amount_owed,
        )
        self._items[item_id] = changed
        return changed

    def record_message(self, message: StoredMessage, files: dict[str, bytes]) -> bool:
        if message.provider_id in self._messages:
            return False
        if message.internet_message_id and message.internet_message_id in self._internet_ids:
            return False
        self._messages[message.provider_id] = replace(message, processed=False)
        if message.internet_message_id:
            self._internet_ids.add(message.internet_message_id)
        self._files.update(files)
        return True

    def unprocessed_messages(self) -> list[StoredMessage]:
        return [message for message in self._messages.values() if not message.processed]

    def mark_processed(self, provider_id: str) -> None:
        self._messages[provider_id] = replace(self._messages[provider_id], processed=True)

    def load_file(self, sha256: str) -> bytes:
        return self._files[sha256]

    def timesheet_seen(self, sha256: str) -> bool:
        return any(record.sha256 == sha256 for record in self._timesheets)

    def record_timesheet(self, record: TimesheetRecord) -> None:
        self._timesheets.append(record)

    def timesheets_for_item(self, item_id: int) -> list[TimesheetRecord]:
        return [record for record in self._timesheets if record.item_id == item_id]

    def open_review(self, item_id: int | None, code: str, message: str) -> bool:
        for review in self._reviews:
            if (
                review.status == "open"
                and review.item_id == item_id
                and review.code == code
                and review.message == message
            ):
                return False
        self._reviews.append(
            ReviewRecord(
                id=self._next_review_id,
                item_id=item_id,
                code=code,
                message=message,
                status="open",
            )
        )
        self._next_review_id += 1
        return True

    def open_reviews(self) -> list[ReviewRecord]:
        return [review for review in self._reviews if review.status == "open"]

    def record_outgoing(
        self, kind: str, idempotency_key: str, item_id: int | None, payload: dict[str, object]
    ) -> bool:
        if idempotency_key in self._outgoing:
            return False
        serialised: dict[str, object] = json.loads(json.dumps(payload))
        self._outgoing[idempotency_key] = OutgoingRecord(
            kind=kind,
            idempotency_key=idempotency_key,
            item_id=item_id,
            payload=serialised,
            status="pending",
        )
        return True

    def outgoing_records(self) -> list[OutgoingRecord]:
        return list(self._outgoing.values())

    def get_state(self, key: str) -> str | None:
        return self._state.get(key)

    def set_state(self, key: str, value: str) -> None:
        self._state[key] = value
