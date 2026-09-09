"""Builders for whole-run scenario tests: a workbook, .eml files, and readings."""

from dataclasses import dataclass, field
from datetime import date
from email.message import EmailMessage
from pathlib import Path

import pytest

from finance_ops_agent.adapters.fakes.accounting import FakeAccounting
from finance_ops_agent.adapters.fakes.clock import FakeClock
from finance_ops_agent.adapters.fakes.engagement_list import FakeEngagementList
from finance_ops_agent.adapters.fakes.mailbox import FakeMailbox
from finance_ops_agent.adapters.fakes.reader import FakeReader
from finance_ops_agent.adapters.fakes.sender import FakeSender
from finance_ops_agent.adapters.fakes.store import FakeStore
from finance_ops_agent.adapters.pdf.writer import TextPdfRenderer
from finance_ops_agent.application.run import Mode, RunDeps, RunReport, Settings, run_once
from finance_ops_agent.domain.engagements import RawRow, RawWorkbook
from finance_ops_agent.domain.items import Item
from finance_ops_agent.domain.reading import (
    Approval,
    ApprovalKind,
    Confidence,
    DailyEntry,
    ReadField,
    ReplyReading,
    TimesheetReading,
)

TODAY = date(2026, 9, 8)
AGENT_ADDRESS = "jay@icon-technologies.com"
PRIYA = "priya@example.com"
DANA = "dana@example.com"


def client_row(row_number: int = 2, **overrides: str) -> RawRow:
    cells = {
        "Client": "Acme Corp",
        "Legal name": "Acme Corporation",
        "Billing contact": "Accounts Payable",
        "Billing email": "ap@acme.example",
        "CC email": "",
        "Payment terms (days)": "30",
        "Delivery": "email",
        "Time system": "",
        "Names on timesheets": "Acme",
        "Email domains": "acme.example",
        "QuickBooks customer": "",
        "Notes": "",
        "Active": "yes",
    }
    cells.update(overrides)
    return RawRow(row_number, cells)


def consultant_row(row_number: int = 2, **overrides: str) -> RawRow:
    cells = {
        "Consultant": "Priya Shah",
        "Other names": "P. Shah",
        "Email": PRIYA,
        "Type": "contractor",
        "Vendor company": "",
        "Paid by": "bank transfer",
        "Pay timing (days)": "15",
        "Active": "yes",
    }
    cells.update(overrides)
    return RawRow(row_number, cells)


def engagement_row(row_number: int = 2, **overrides: str) -> RawRow:
    cells = {
        "Consultant": "Priya Shah",
        "Client": "Acme Corp",
        "End client": "",
        "Role": "Senior PeopleSoft Developer",
        "Start date": "2026-08-01",
        "End date": "",
        "Billing schedule": "monthly",
        "First period start": "",
        "Bill rate": "140.00",
        "Pay rate": "100.00",
        "Rates from": "2026-08-01",
        "Send automatically": "no",
        "Active": "yes",
    }
    cells.update(overrides)
    return RawRow(row_number, cells)


def default_workbook() -> RawWorkbook:
    return RawWorkbook(
        clients=[client_row()],
        consultants=[
            consultant_row(),
            consultant_row(3, Consultant="Dana Cruz", Email=DANA, **{"Other names": ""}),
        ],
        vendors=[],
        engagements=[
            engagement_row(),
            engagement_row(
                3,
                Consultant="Dana Cruz",
                **{"Start date": "2026-09-01", "Rates from": "2026-09-01"},
            ),
        ],
    )


def reading(
    start: date,
    end: date,
    total_hundredths: int | None = 15_600,
    consultant: str = "Priya Shah",
    client: str = "Acme Corp",
    dailies: list[tuple[date, int]] | None = None,
    approved: bool = True,
    confidence: Confidence = Confidence.HIGH,
) -> TimesheetReading:
    approval = (
        Approval(kind=ApprovalKind.APPROVED_STATUS, approver="Jane Doe", approval_date=end)
        if approved
        else Approval(kind=ApprovalKind.NONE)
    )
    return TimesheetReading(
        consultant_name=ReadField[str](value=consultant, quote=consultant, confidence=confidence),
        client_name=ReadField[str](value=client, quote=client, confidence=confidence),
        end_client_name=ReadField[str](confidence=Confidence.LOW),
        period_start=ReadField[date](value=start, quote=str(start), confidence=confidence),
        period_end=ReadField[date](value=end, quote=str(end), confidence=confidence),
        daily_entries=ReadField[list[DailyEntry]](
            value=[DailyEntry(day=day, hours_hundredths=hours) for day, hours in dailies or []],
            confidence=confidence,
        ),
        stated_total_hours_hundredths=ReadField[int](
            value=total_hundredths,
            quote=None if total_hundredths is None else str(total_hundredths),
            confidence=confidence,
        ),
        approval=ReadField[Approval](
            value=approval,
            quote="Approved" if approved else None,
            confidence=confidence,
        ),
    )


@dataclass
class ScenarioEnv:
    mailbox_dir: Path
    mailbox: FakeMailbox
    store: FakeStore
    readings: dict[str, TimesheetReading]
    workbook: RawWorkbook
    sender: FakeSender
    accounting: FakeAccounting
    email_count: int = 0
    today: date = TODAY
    mode: Mode = Mode.DRY_RUN
    replies: dict[str, ReplyReading] = field(default_factory=dict)
    _deps: RunDeps | None = field(default=None, repr=False)

    def add_email(
        self,
        from_address: str,
        subject: str = "Timesheet attached",
        attachment: tuple[str, bytes] | None = ("timesheet.pdf", b"PDFDATA"),
        message_id: str | None = None,
        scripted_reading: TimesheetReading | None = None,
        body: str = "Please see attached.",
    ) -> str:
        self.email_count += 1
        name = f"{self.email_count:02d}-email"
        message = EmailMessage()
        message["From"] = from_address
        message["To"] = AGENT_ADDRESS
        message["Subject"] = subject
        message["Message-ID"] = message_id or f"<{name}@example>"
        message["Date"] = "Tue, 08 Sep 2026 09:00:00 +0000"
        message.set_content(body)
        if attachment is not None:
            filename, content = attachment
            message.add_attachment(
                content, maintype="application", subtype="pdf", filename=filename
            )
            if scripted_reading is not None:
                self.readings[filename] = scripted_reading
        (self.mailbox_dir / f"{name}.eml").write_bytes(bytes(message))
        return name

    def deps(self) -> RunDeps:
        return RunDeps(
            engagement_list=FakeEngagementList(self.workbook),
            inbox=self.mailbox,
            reader=FakeReader(self.readings, self.replies),
            store=self.store,
            clock=FakeClock(self.today),
            settings=Settings(mode=self.mode),
            sender=self.sender,
            accounting=self.accounting,
            renderer=TextPdfRenderer(),
        )

    def run(self) -> RunReport:
        return run_once(self.deps())

    def report(self) -> RunReport:
        return RunReport()

    def sent_subjects(self) -> list[str]:
        return [email.subject for email in self.sender.sent_emails()]

    def reply_from_kevin(self, original_subject: str, body: str) -> str:
        return self.add_email(
            "kevin@icon-technologies.com",
            subject=f"Re: {original_subject}",
            attachment=None,
            body=body,
        )

    def the_item(self) -> Item:
        items = self.store.list_items()
        assert len(items) == 1, [str(item) for item in items]
        return items[0]


@pytest.fixture
def env(tmp_path: Path) -> ScenarioEnv:
    mailbox_dir = tmp_path / "mailbox"
    mailbox_dir.mkdir()
    return ScenarioEnv(
        mailbox_dir=mailbox_dir,
        mailbox=FakeMailbox(mailbox_dir),
        store=FakeStore(),
        readings={},
        workbook=default_workbook(),
        sender=FakeSender(tmp_path / "outbox"),
        accounting=FakeAccounting(),
    )
