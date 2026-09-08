"""The SQLite implementation of the Store port."""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Engine, create_engine, event, select
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import ConnectionPoolEntry

from finance_ops_agent.adapters.sqlite.migrations import upgrade_to_head
from finance_ops_agent.adapters.sqlite.schema import AuditLogRow, ItemRow
from finance_ops_agent.domain.items import AuditEntry, EngagementSnapshot, Item
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


def open_database(path: Path | str) -> Engine:
    """Open (and migrate) the database with WAL, a busy timeout, and foreign keys on."""
    engine = create_engine(f"sqlite:///{path}")

    @event.listens_for(engine, "connect")
    def _configure(dbapi_connection: DBAPIConnection, _record: ConnectionPoolEntry) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    upgrade_to_head(engine)
    return engine


class SqliteStore:
    def __init__(self, engine: Engine, now: Callable[[], datetime] = _utcnow) -> None:
        self._engine = engine
        self._now = now

    def _audit(
        self, session: Session, what: str, item_id: int | None, details: dict[str, object]
    ) -> None:
        # Round-trip through json here, inside the transaction: details that
        # cannot be written down roll the whole change back.
        session.add(
            AuditLogRow(
                at=self._now().isoformat(),
                what=what,
                item_id=item_id,
                details=json.loads(json.dumps(details)),
            )
        )

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
        with Session(self._engine) as session, session.begin():
            row = ItemRow(
                consultant=consultant,
                client=client,
                period_start=period.start,
                period_end=period.end,
                status=status.value,
                engagement_snapshot=snapshot.model_dump(),
                approved_hours_hundredths=None,
                invoice_amount_cents=None,
                amount_owed_cents=None,
            )
            session.add(row)
            try:
                session.flush()
            except IntegrityError as error:
                raise DuplicateItemError(
                    f"there is already an item for {consultant} at {client}"
                    f" for {period.start} to {period.end}"
                ) from error
            self._audit(session, "item created", row.id, {"status": status.value})
            return _to_item(row)

    def get_item(self, item_id: int) -> Item:
        with Session(self._engine) as session:
            row = session.get(ItemRow, item_id)
            if row is None:
                raise KeyError(item_id)
            return _to_item(row)

    def find_item(self, consultant: str, client: str, period: BillingPeriod) -> Item | None:
        with Session(self._engine) as session:
            row = session.scalars(
                select(ItemRow).where(
                    ItemRow.consultant == consultant,
                    ItemRow.client == client,
                    ItemRow.period_start == period.start,
                    ItemRow.period_end == period.end,
                )
            ).one_or_none()
            return None if row is None else _to_item(row)

    def list_items(self) -> list[Item]:
        with Session(self._engine) as session:
            return [_to_item(row) for row in session.scalars(select(ItemRow).order_by(ItemRow.id))]

    def change_status(
        self, item_id: int, new_status: ItemStatus, details: dict[str, object]
    ) -> Item:
        with Session(self._engine) as session, session.begin():
            row = session.get(ItemRow, item_id)
            if row is None:
                raise KeyError(item_id)
            current = ItemStatus(row.status)
            change_status(current, new_status)
            row.status = new_status.value
            self._audit(
                session,
                "status changed",
                item_id,
                {"from": current.value, "to": new_status.value, **details},
            )
            session.flush()
            return _to_item(row)

    def audit_entries(self, item_id: int | None = None) -> list[AuditEntry]:
        with Session(self._engine) as session:
            query = select(AuditLogRow).order_by(AuditLogRow.id)
            if item_id is not None:
                query = query.where(AuditLogRow.item_id == item_id)
            return [
                AuditEntry(
                    at=datetime.fromisoformat(row.at),
                    what=row.what,
                    item_id=row.item_id,
                    details=dict(row.details),
                )
                for row in session.scalars(query)
            ]


def _to_item(row: ItemRow) -> Item:
    return Item(
        id=row.id,
        consultant=row.consultant,
        client=row.client,
        period=BillingPeriod(row.period_start, row.period_end),
        status=ItemStatus(row.status),
        snapshot=EngagementSnapshot.model_validate(row.engagement_snapshot),
        approved_hours=(
            None if row.approved_hours_hundredths is None else Hours(row.approved_hours_hundredths)
        ),
        invoice_amount=(
            None if row.invoice_amount_cents is None else Money(row.invoice_amount_cents)
        ),
        amount_owed=None if row.amount_owed_cents is None else Money(row.amount_owed_cents),
    )
