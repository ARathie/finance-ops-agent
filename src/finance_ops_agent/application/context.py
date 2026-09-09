"""What every step of a run shares: the ports, the settings, and the report."""

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from finance_ops_agent.domain.tracking import TrackingRow
from finance_ops_agent.ports.accounting import AccountingSystem
from finance_ops_agent.ports.clock import Clock
from finance_ops_agent.ports.engagement_list import EngagementList
from finance_ops_agent.ports.inbox import EmailInbox
from finance_ops_agent.ports.pdf import PdfRenderer
from finance_ops_agent.ports.reader import TimesheetReader
from finance_ops_agent.ports.sender import EmailSender
from finance_ops_agent.ports.store import Store


class Mode(StrEnum):
    """The three modes (docs/how-it-works.md). dry_run is the kill switch."""

    DRY_RUN = "dry_run"
    ASK_FIRST = "ask_first"
    AUTO = "auto"


@dataclass(frozen=True)
class Settings:
    admin_email: str = "kevin@icon-technologies.com"
    mode: Mode = Mode.DRY_RUN


@dataclass
class RunDeps:
    engagement_list: EngagementList
    inbox: EmailInbox
    reader: TimesheetReader
    store: Store
    clock: Clock
    settings: Settings
    sender: EmailSender
    accounting: AccountingSystem
    renderer: PdfRenderer
    tracking_path: Path | None = None
    render_tracking: Callable[[list[TrackingRow]], bytes] | None = None


@dataclass
class RunReport:
    messages_stored: int = 0
    timesheets_processed: int = 0
    duplicates_filed: int = 0
    reviews_opened: int = 0
    items_made_ready: int = 0
    expected_items_created: int = 0
    unknown_senders: int = 0
    emails_sent: int = 0
    invoices_created: int = 0
    lines: list[str] = field(default_factory=list)

    def note(self, line: str) -> None:
        self.lines.append(line)
