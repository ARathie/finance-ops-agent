"""The Store contract: the fake and the SQLite adapter behave identically."""

from collections.abc import Callable
from datetime import date
from pathlib import Path

import pytest

from finance_ops_agent.adapters.fakes.store import FakeStore
from finance_ops_agent.adapters.sqlite.store import SqliteStore, open_database
from finance_ops_agent.domain.items import EngagementSnapshot
from finance_ops_agent.domain.periods import BillingPeriod
from finance_ops_agent.domain.statuses import DisallowedStatusChange, ItemStatus
from finance_ops_agent.ports.store import DuplicateItemError, Store

AUGUST = BillingPeriod(date(2026, 8, 1), date(2026, 8, 31))
SEPTEMBER = BillingPeriod(date(2026, 9, 1), date(2026, 9, 30))


def snapshot() -> EngagementSnapshot:
    return EngagementSnapshot(
        bill_rate_cents=14_000,
        pay_rate_cents=10_000,
        payment_terms_days=30,
        pay_timing_days=15,
        billing_emails=["ap@acme.example"],
        cc_emails=[],
        payee="Priya Shah",
        paid_by="bank transfer",
        engagement_row_number=2,
    )


@pytest.fixture(params=["fake", "sqlite"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Store:
    if request.param == "fake":
        return FakeStore()
    return SqliteStore(open_database(tmp_path / "agent.db"))


def create(store: Store, period: BillingPeriod = AUGUST, client: str = "Acme Corp") -> int:
    item = store.create_item("Priya Shah", client, period, ItemStatus.RECEIVED, snapshot())
    return item.id


class TestOneRecordPerConsultantPerPeriod:
    def test_creating_and_finding(self, store: Store) -> None:
        item_id = create(store)
        found = store.find_item("Priya Shah", "Acme Corp", AUGUST)
        assert found is not None
        assert found.id == item_id
        assert found.status is ItemStatus.RECEIVED
        assert found.snapshot.bill_rate_cents == 14_000
        assert store.get_item(item_id) == found
        assert store.list_items() == [found]

    def test_a_second_item_for_the_same_period_is_refused(self, store: Store) -> None:
        create(store)
        with pytest.raises(DuplicateItemError):
            create(store)
        assert len(store.list_items()) == 1

    def test_other_periods_and_clients_are_fine(self, store: Store) -> None:
        create(store)
        create(store, period=SEPTEMBER)
        create(store, client="Northwind Bank")
        assert len(store.list_items()) == 3

    def test_an_item_cannot_start_in_a_non_initial_status(self, store: Store) -> None:
        with pytest.raises(ValueError):
            store.create_item(
                "Priya Shah", "Acme Corp", AUGUST, ItemStatus.INVOICE_SENT, snapshot()
            )

    def test_missing_item_raises(self, store: Store) -> None:
        with pytest.raises(KeyError):
            store.get_item(999)


class TestStatusChangesAndTheAuditLog:
    def test_creation_writes_an_audit_row(self, store: Store) -> None:
        item_id = create(store)
        entries = store.audit_entries(item_id)
        assert [entry.what for entry in entries] == ["item created"]

    def test_a_status_change_writes_an_audit_row(self, store: Store) -> None:
        item_id = create(store)
        changed = store.change_status(item_id, ItemStatus.READY, {"why": "all checks passed"})
        assert changed.status is ItemStatus.READY
        assert store.get_item(item_id).status is ItemStatus.READY
        entry = store.audit_entries(item_id)[-1]
        assert entry.what == "status changed"
        assert entry.details["from"] == "received"
        assert entry.details["to"] == "ready"
        assert entry.details["why"] == "all checks passed"

    def test_a_disallowed_change_changes_nothing(self, store: Store) -> None:
        item_id = create(store)
        with pytest.raises(DisallowedStatusChange):
            store.change_status(item_id, ItemStatus.CLIENT_PAID, {})
        assert store.get_item(item_id).status is ItemStatus.RECEIVED
        assert len(store.audit_entries(item_id)) == 1  # just "item created"

    def test_the_change_and_its_audit_row_are_all_or_nothing(self, store: Store) -> None:
        # Details that cannot be written down must roll the whole change back.
        item_id = create(store)
        with pytest.raises(TypeError):
            store.change_status(item_id, ItemStatus.READY, {"bad": object()})
        assert store.get_item(item_id).status is ItemStatus.RECEIVED
        assert len(store.audit_entries(item_id)) == 1

    def test_audit_entries_come_back_oldest_first(self, store: Store) -> None:
        item_id = create(store)
        store.change_status(item_id, ItemStatus.NEEDS_REVIEW, {})
        store.change_status(item_id, ItemStatus.READY, {})
        whats = [entry.what for entry in store.audit_entries(item_id)]
        assert whats == ["item created", "status changed", "status changed"]
        assert store.audit_entries() == store.audit_entries(item_id)


def test_the_database_survives_reopening(tmp_path: Path) -> None:
    path = tmp_path / "agent.db"
    first = SqliteStore(open_database(path))
    item_id = create(first)
    first.change_status(item_id, ItemStatus.READY, {})

    reopened = SqliteStore(open_database(path))  # migrations run again: no-op
    assert reopened.get_item(item_id).status is ItemStatus.READY
    assert len(reopened.audit_entries(item_id)) == 2


def test_audit_timestamps_are_utc(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    fixed = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
    stores: list[Callable[[], Store]] = [
        lambda: FakeStore(now=lambda: fixed),
        lambda: SqliteStore(open_database(tmp_path / "t.db"), now=lambda: fixed),
    ]
    for make in stores:
        store = make()
        item_id = create(store)
        assert store.audit_entries(item_id)[0].at == fixed
