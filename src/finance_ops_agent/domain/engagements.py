"""The engagement list as typed rows, parsed and checked from the raw workbook.

The workbook is Kevin's spreadsheet (docs/engagement-list.md) and is the only
source of rates, contacts, and terms. Adapters read the cells as text; this
module turns them into typed rows. A row that is incomplete or contradicts
another row becomes a ListRowProblem naming the sheet and row, which the
application turns into a LIST_ROW_PROBLEM review item; the row itself is left
out of the typed lists so nothing is ever billed from a bad row.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import TypeVar

from finance_ops_agent.domain.money import Money
from finance_ops_agent.domain.periods import BillingSchedule

_R = TypeVar("_R", "Client", "Consultant", "Vendor", "Engagement")


@dataclass(frozen=True)
class RawRow:
    """One spreadsheet row as text: the row number Kevin sees, and cell text by column name."""

    row_number: int
    cells: Mapping[str, str]


@dataclass(frozen=True)
class RawWorkbook:
    clients: list[RawRow]
    consultants: list[RawRow]
    vendors: list[RawRow]
    engagements: list[RawRow]


@dataclass(frozen=True)
class ListRowProblem:
    """Told to Kevin in a LIST_ROW_PROBLEM review email, naming the sheet and row."""

    sheet: str
    row_number: int
    message: str


class Delivery(StrEnum):
    EMAIL = "email"
    PORTAL = "portal"


class ConsultantType(StrEnum):
    EMPLOYEE = "employee"
    CONTRACTOR = "contractor"
    VENDOR = "vendor"


class PaidBy(StrEnum):
    BANK_TRANSFER = "bank transfer"
    PAYROLL = "payroll"
    CHECK = "check"
    OTHER = "other"


@dataclass(frozen=True)
class Client:
    name: str
    legal_name: str
    billing_contact: str
    billing_emails: tuple[str, ...]
    cc_emails: tuple[str, ...]
    payment_terms_days: int
    delivery: Delivery
    time_system: str
    names_on_timesheets: tuple[str, ...]
    email_domains: tuple[str, ...]
    quickbooks_customer: str
    notes: str
    active: bool
    row_number: int


@dataclass(frozen=True)
class Consultant:
    name: str
    other_names: tuple[str, ...]
    emails: tuple[str, ...]
    type: ConsultantType
    vendor_company: str
    paid_by: PaidBy
    pay_timing_days: int
    active: bool
    row_number: int


@dataclass(frozen=True)
class Vendor:
    company: str
    contact_emails: tuple[str, ...]
    paid_by: PaidBy
    pay_timing_days: int
    active: bool
    row_number: int


@dataclass(frozen=True)
class Engagement:
    consultant: str
    client: str
    end_client: str
    role: str
    start_date: date
    end_date: date | None
    billing_schedule: BillingSchedule
    first_period_start: date | None
    bill_rate: Money
    pay_rate: Money
    rates_from: date
    send_automatically: bool
    active: bool
    row_number: int


@dataclass(frozen=True)
class EngagementWorkbook:
    """The typed rows that parsed cleanly, plus a problem per row that did not."""

    clients: list[Client]
    consultants: list[Consultant]
    vendors: list[Vendor]
    engagements: list[Engagement]
    problems: list[ListRowProblem]


class _RowReader:
    """Reads one raw row, collecting what is wrong with it."""

    def __init__(self, sheet: str, raw: RawRow) -> None:
        self.sheet = sheet
        self.raw = raw
        self.errors: list[str] = []

    def text(self, column: str) -> str:
        return self.raw.cells.get(column, "").strip()

    def required(self, column: str) -> str:
        value = self.text(column)
        if not value:
            self.errors.append(f'"{column}" is empty')
        return value

    def emails(self, column: str) -> tuple[str, ...]:
        return _split(self.text(column))

    def days(self, column: str, *, required: bool = True) -> int:
        value = self.required(column) if required else self.text(column)
        if not value:
            return 0
        try:
            days = int(value)
        except ValueError:
            self.errors.append(f'"{column}" should be a number of days, not {value!r}')
            return 0
        if days < 0:
            self.errors.append(f'"{column}" should not be negative')
            return 0
        return days

    def yes_no(self, column: str, *, blank_means_no: bool = False) -> bool:
        value = self.text(column).lower()
        if not value and blank_means_no:
            return False
        if value not in ("yes", "no"):
            self.errors.append(f'"{column}" should be yes or no, not {self.text(column)!r}')
            return False
        return value == "yes"

    def choice(self, column: str, allowed: type[StrEnum], *, forbid: set[str] | None = None) -> str:
        value = self.required(column).lower()
        allowed_values = {item.value for item in allowed} - (forbid or set())
        if value and value not in allowed_values:
            choices = ", ".join(sorted(allowed_values))
            self.errors.append(f'"{column}" should be one of {choices}, not {self.text(column)!r}')
            return ""
        return value

    def money(self, column: str) -> Money:
        value = self.required(column)
        if not value:
            return Money(0)
        try:
            return Money.parse(value)
        except ValueError:
            self.errors.append(f'"{column}" should be a dollar amount like 140.00, not {value!r}')
            return Money(0)

    def date(self, column: str, *, required: bool = True) -> date | None:
        value = self.required(column) if required else self.text(column)
        if not value:
            return None
        try:
            return date.fromisoformat(value)
        except ValueError:
            self.errors.append(f'"{column}" should be a date like 2026-02-01, not {value!r}')
            return None

    def problems(self) -> list[ListRowProblem]:
        return [ListRowProblem(self.sheet, self.raw.row_number, message) for message in self.errors]


def _split(text: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in text.split(";") if part.strip())


def _key(name: str) -> str:
    return name.strip().casefold()


def _parse_client(raw: RawRow) -> tuple[Client | None, list[ListRowProblem]]:
    row = _RowReader("Clients", raw)
    name = row.required("Client")
    legal_name = row.required("Legal name")
    delivery_value = row.choice("Delivery", Delivery)
    billing_emails = row.emails("Billing email")
    if delivery_value == Delivery.EMAIL.value and not billing_emails:
        row.errors.append("there is no billing email for a client delivered by email")
    client = Client(
        name=name,
        legal_name=legal_name,
        billing_contact=row.text("Billing contact"),
        billing_emails=billing_emails,
        cc_emails=row.emails("CC email"),
        payment_terms_days=row.days("Payment terms (days)"),
        delivery=Delivery(delivery_value) if delivery_value else Delivery.EMAIL,
        time_system=row.text("Time system"),
        names_on_timesheets=_split(row.text("Names on timesheets")),
        email_domains=_split(row.text("Email domains")),
        quickbooks_customer=row.text("QuickBooks customer"),
        notes=row.text("Notes"),
        active=row.yes_no("Active"),
        row_number=raw.row_number,
    )
    return (client if not row.errors else None), row.problems()


def _parse_consultant(raw: RawRow) -> tuple[Consultant | None, list[ListRowProblem]]:
    row = _RowReader("Consultants", raw)
    name = row.required("Consultant")
    type_value = row.choice("Type", ConsultantType)
    vendor_company = row.text("Vendor company")
    if type_value == ConsultantType.VENDOR.value and not vendor_company:
        row.errors.append('"Type" is vendor but "Vendor company" is empty')
    paid_by_value = row.choice("Paid by", PaidBy)
    consultant = Consultant(
        name=name,
        other_names=_split(row.text("Other names")),
        emails=row.emails("Email"),
        type=ConsultantType(type_value) if type_value else ConsultantType.CONTRACTOR,
        vendor_company=vendor_company,
        paid_by=PaidBy(paid_by_value) if paid_by_value else PaidBy.OTHER,
        pay_timing_days=row.days("Pay timing (days)"),
        active=row.yes_no("Active"),
        row_number=raw.row_number,
    )
    return (consultant if not row.errors else None), row.problems()


def _parse_vendor(raw: RawRow) -> tuple[Vendor | None, list[ListRowProblem]]:
    row = _RowReader("Vendors", raw)
    company = row.required("Vendor company")
    # Vendors are paid by bank transfer, check, or other -- never payroll.
    paid_by_value = row.choice("Paid by", PaidBy, forbid={PaidBy.PAYROLL.value})
    vendor = Vendor(
        company=company,
        contact_emails=row.emails("Contact email"),
        paid_by=PaidBy(paid_by_value) if paid_by_value else PaidBy.OTHER,
        pay_timing_days=row.days("Pay timing (days)"),
        active=row.yes_no("Active"),
        row_number=raw.row_number,
    )
    return (vendor if not row.errors else None), row.problems()


def _parse_engagement(raw: RawRow) -> tuple[Engagement | None, list[ListRowProblem]]:
    row = _RowReader("Engagements", raw)
    consultant = row.required("Consultant")
    client = row.required("Client")
    schedule_value = row.choice("Billing schedule", BillingSchedule)
    first_period_start = row.date("First period start", required=False)
    if (
        schedule_value in (BillingSchedule.WEEKLY.value, BillingSchedule.EVERY_TWO_WEEKS.value)
        and first_period_start is None
    ):
        row.errors.append(
            f'"First period start" is needed when the billing schedule is {schedule_value}'
        )
    bill_rate = row.money("Bill rate")
    pay_rate = row.money("Pay rate")
    if bill_rate.cents and pay_rate.cents > bill_rate.cents:
        row.errors.append(f"the pay rate ({pay_rate}) is higher than the bill rate ({bill_rate})")
    start_date = row.date("Start date")
    end_date = row.date("End date", required=False)
    if start_date and end_date and end_date < start_date:
        row.errors.append('"End date" is before "Start date"')
    rates_from = row.date("Rates from")
    engagement = Engagement(
        consultant=consultant,
        client=client,
        end_client=row.text("End client"),
        role=row.required("Role"),
        start_date=start_date or date.min,
        end_date=end_date,
        billing_schedule=BillingSchedule(schedule_value)
        if schedule_value
        else BillingSchedule.MONTHLY,
        first_period_start=first_period_start,
        bill_rate=bill_rate,
        pay_rate=pay_rate,
        rates_from=rates_from or date.min,
        send_automatically=row.yes_no("Send automatically", blank_means_no=True),
        active=row.yes_no("Active"),
        row_number=raw.row_number,
    )
    return (engagement if not row.errors else None), row.problems()


def _parse_sheet(
    raws: list[RawRow],
    parse: "Callable[[RawRow], tuple[_R | None, list[ListRowProblem]]]",
    problems: list[ListRowProblem],
) -> "list[_R]":
    rows: list[_R] = []
    for raw in raws:
        row, row_problems = parse(raw)
        problems.extend(row_problems)
        if row is not None:
            rows.append(row)
    return rows


def parse_workbook(raw: RawWorkbook) -> EngagementWorkbook:
    problems: list[ListRowProblem] = []
    clients = _parse_sheet(raw.clients, _parse_client, problems)
    consultants = _parse_sheet(raw.consultants, _parse_consultant, problems)
    vendors = _parse_sheet(raw.vendors, _parse_vendor, problems)
    engagements = _parse_sheet(raw.engagements, _parse_engagement, problems)

    client_names = {_key(client.name) for client in clients}
    consultant_names = {_key(consultant.name) for consultant in consultants}
    vendor_names = {_key(vendor.company) for vendor in vendors}

    for consultant in consultants:
        if (
            consultant.type is ConsultantType.VENDOR
            and _key(consultant.vendor_company) not in vendor_names
        ):
            problems.append(
                ListRowProblem(
                    "Consultants",
                    consultant.row_number,
                    f'"Vendor company" {consultant.vendor_company!r} is not on the Vendors sheet',
                )
            )

    checked_engagements: list[Engagement] = []
    seen_rate_rows: dict[tuple[str, str, date], int] = {}
    for engagement in engagements:
        ok = True
        if _key(engagement.consultant) not in consultant_names:
            problems.append(
                ListRowProblem(
                    "Engagements",
                    engagement.row_number,
                    f'"Consultant" {engagement.consultant!r} is not on the Consultants sheet',
                )
            )
            ok = False
        if _key(engagement.client) not in client_names:
            problems.append(
                ListRowProblem(
                    "Engagements",
                    engagement.row_number,
                    f'"Client" {engagement.client!r} is not on the Clients sheet',
                )
            )
            ok = False
        rate_key = (_key(engagement.consultant), _key(engagement.client), engagement.rates_from)
        earlier_row = seen_rate_rows.get(rate_key)
        if earlier_row is not None:
            problems.append(
                ListRowProblem(
                    "Engagements",
                    engagement.row_number,
                    "this row has the same consultant, client, and"
                    f' "Rates from" date as row {earlier_row}',
                )
            )
            ok = False
        else:
            seen_rate_rows[rate_key] = engagement.row_number
        if ok:
            checked_engagements.append(engagement)

    return EngagementWorkbook(
        clients=clients,
        consultants=consultants,
        vendors=vendors,
        engagements=checked_engagements,
        problems=problems,
    )
