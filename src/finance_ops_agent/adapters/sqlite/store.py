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
from finance_ops_agent.adapters.sqlite.schema import (
    AttachmentRow,
    AuditLogRow,
    ItemRow,
    MessageRow,
    OutgoingRow,
    ReviewItemRow,
    StateRow,
    TimesheetRow,
)
from finance_ops_agent.domain.items import (
    AuditEntry,
    EngagementSnapshot,
    Item,
    OutgoingRecord,
    ReviewRecord,
    TimesheetRecord,
)
from finance_ops_agent.domain.messages import MessageKind, StoredAttachment, StoredMessage
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
    def __init__(
        self,
        engine: Engine,
        files_dir: Path | None = None,
        now: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._engine = engine
        self._files_dir = files_dir
        self._now = now

    def _file_path(self, sha256: str) -> Path:
        if self._files_dir is None:
            raise RuntimeError("this store was opened without a files directory")
        return self._files_dir / sha256

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

    def set_item_amounts(
        self, item_id: int, approved_hours: Hours, invoice_amount: Money, amount_owed: Money
    ) -> Item:
        with Session(self._engine) as session, session.begin():
            row = session.get(ItemRow, item_id)
            if row is None:
                raise KeyError(item_id)
            row.approved_hours_hundredths = approved_hours.hundredths
            row.invoice_amount_cents = invoice_amount.cents
            row.amount_owed_cents = amount_owed.cents
            session.flush()
            return _to_item(row)

    def record_message(self, message: StoredMessage, files: dict[str, bytes]) -> bool:
        with Session(self._engine) as session, session.begin():
            already = session.scalars(
                select(MessageRow).where(MessageRow.provider_id == message.provider_id)
            ).first()
            if already is None and message.internet_message_id:
                already = session.scalars(
                    select(MessageRow).where(
                        MessageRow.internet_message_id == message.internet_message_id
                    )
                ).first()
            if already is not None:
                return False
            row = MessageRow(
                provider_id=message.provider_id,
                internet_message_id=message.internet_message_id or None,
                conversation_id=message.conversation_id or None,
                from_address=message.from_address,
                to_addresses=message.to_addresses,
                subject=message.subject,
                received_at=message.received_at.isoformat(),
                kind=message.kind.value,
                processed_at=None,
            )
            session.add(row)
            session.flush()
            for attachment in message.attachments:
                session.add(
                    AttachmentRow(
                        message_id=row.id,
                        filename=attachment.filename,
                        mime_type=attachment.mime_type,
                        sha256=attachment.sha256,
                        size_bytes=attachment.size_bytes,
                    )
                )
        # Content-addressed files: writing the same bytes twice is harmless.
        for sha256, content in files.items():
            path = self._file_path(sha256)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        return True

    def _stored_message(self, session: Session, row: MessageRow) -> StoredMessage:
        attachments = tuple(
            StoredAttachment(
                filename=attachment.filename,
                mime_type=attachment.mime_type,
                sha256=attachment.sha256,
                size_bytes=attachment.size_bytes,
            )
            for attachment in session.scalars(
                select(AttachmentRow)
                .where(AttachmentRow.message_id == row.id)
                .order_by(AttachmentRow.id)
            )
        )
        return StoredMessage(
            provider_id=row.provider_id,
            internet_message_id=row.internet_message_id or "",
            conversation_id=row.conversation_id or "",
            from_address=row.from_address,
            to_addresses=row.to_addresses,
            subject=row.subject,
            received_at=datetime.fromisoformat(row.received_at),
            kind=MessageKind(row.kind),
            processed=row.processed_at is not None,
            attachments=attachments,
        )

    def unprocessed_messages(self) -> list[StoredMessage]:
        with Session(self._engine) as session:
            rows = session.scalars(
                select(MessageRow).where(MessageRow.processed_at.is_(None)).order_by(MessageRow.id)
            )
            return [self._stored_message(session, row) for row in rows]

    def mark_processed(self, provider_id: str) -> None:
        with Session(self._engine) as session, session.begin():
            row = session.scalars(
                select(MessageRow).where(MessageRow.provider_id == provider_id)
            ).one()
            row.processed_at = self._now().isoformat()

    def load_file(self, sha256: str) -> bytes:
        return self._file_path(sha256).read_bytes()

    def timesheet_seen(self, sha256: str) -> bool:
        with Session(self._engine) as session:
            return (
                session.scalars(
                    select(TimesheetRow).where(TimesheetRow.attachment_sha256 == sha256)
                ).first()
                is not None
            )

    def record_timesheet(self, record: TimesheetRecord) -> None:
        with Session(self._engine) as session, session.begin():
            session.add(
                TimesheetRow(
                    item_id=record.item_id,
                    attachment_sha256=record.sha256,
                    reading=record.reading,
                    model=record.model,
                    prompt_version=record.prompt_version,
                    is_duplicate=record.is_duplicate,
                    is_correction=record.is_correction,
                )
            )

    def timesheets_for_item(self, item_id: int) -> list[TimesheetRecord]:
        with Session(self._engine) as session:
            rows = session.scalars(
                select(TimesheetRow)
                .where(TimesheetRow.item_id == item_id)
                .order_by(TimesheetRow.id)
            )
            return [
                TimesheetRecord(
                    item_id=row.item_id,
                    sha256=row.attachment_sha256,
                    reading=dict(row.reading),
                    model=row.model,
                    prompt_version=row.prompt_version,
                    is_duplicate=row.is_duplicate,
                    is_correction=row.is_correction,
                )
                for row in rows
            ]

    def open_review(self, item_id: int | None, code: str, message: str) -> bool:
        with Session(self._engine) as session, session.begin():
            existing = session.scalars(
                select(ReviewItemRow).where(
                    ReviewItemRow.item_id == item_id,
                    ReviewItemRow.code == code,
                    ReviewItemRow.message == message,
                    ReviewItemRow.status == "open",
                )
            ).first()
            if existing is not None:
                return False
            session.add(
                ReviewItemRow(
                    item_id=item_id,
                    message_id=None,
                    code=code,
                    message=message,
                    status="open",
                    answer=None,
                    answered_at=None,
                )
            )
            return True

    def open_reviews(self) -> list[ReviewRecord]:
        with Session(self._engine) as session:
            rows = session.scalars(
                select(ReviewItemRow)
                .where(ReviewItemRow.status == "open")
                .order_by(ReviewItemRow.id)
            )
            return [
                ReviewRecord(
                    id=row.id,
                    item_id=row.item_id,
                    code=row.code,
                    message=row.message,
                    status=row.status,
                )
                for row in rows
            ]

    def record_outgoing(
        self, kind: str, idempotency_key: str, item_id: int | None, payload: dict[str, object]
    ) -> bool:
        with Session(self._engine) as session:
            session.add(
                OutgoingRow(
                    kind=kind,
                    idempotency_key=idempotency_key,
                    item_id=item_id,
                    payload=json.loads(json.dumps(payload)),
                    status="pending",
                    draft_id=None,
                    external_id=None,
                    attempts=0,
                    last_error=None,
                )
            )
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                return False
            return True

    def outgoing_records(self) -> list[OutgoingRecord]:
        with Session(self._engine) as session:
            rows = session.scalars(select(OutgoingRow).order_by(OutgoingRow.id))
            return [
                OutgoingRecord(
                    kind=row.kind,
                    idempotency_key=row.idempotency_key,
                    item_id=row.item_id,
                    payload=dict(row.payload),
                    status=row.status,
                )
                for row in rows
            ]

    def get_state(self, key: str) -> str | None:
        with Session(self._engine) as session:
            row = session.get(StateRow, key)
            return None if row is None else row.value

    def set_state(self, key: str, value: str) -> None:
        with Session(self._engine) as session, session.begin():
            row = session.get(StateRow, key)
            if row is None:
                session.add(StateRow(key=key, value=value))
            else:
                row.value = value


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
