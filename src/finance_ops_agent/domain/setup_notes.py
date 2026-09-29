"""Icon's own rules, written as labelled lines in QuickBooks (decision 53).

QuickBooks has no field for a client's invoice code or for the day an
engagement started, so Kevin writes them as `Label: value` lines: in a
customer's Notes box, and in a product's "Description on purchase forms"
(products have no Notes box). Anything else in the box is his and is ignored.

A line with a label this module knows and a value it cannot read is a problem,
never a guess. A known label written twice with different values is a problem
too. A label it does not know is not: "Contact: Bob" is Kevin's own note, and
refusing it would make the box unusable for anything else.
"""

import re
from dataclasses import dataclass, field
from datetime import date, datetime

from finance_ops_agent.domain.engagements import Delivery
from finance_ops_agent.domain.periods import BillingSchedule

CUSTOMER_LABELS = ("invoice code", "delivery", "cc")
PRODUCT_LABELS = ("start", "schedule", "first period", "send automatically")

_LINE = re.compile(r"^\s*([A-Za-z][A-Za-z ]*?)\s*:\s*(.*?)\s*$")
_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y")
_SCHEDULES = {schedule.value: schedule for schedule in BillingSchedule}


def labelled_lines(text: str, labels: tuple[str, ...]) -> tuple[dict[str, str], list[str]]:
    """The known labels in a box and their values, and what is wrong with them.

    Labels are matched ignoring case and extra spaces. Non-breaking spaces
    arrive from pasting (decision 47), so they count as spaces.
    """
    values: dict[str, str] = {}
    problems: list[str] = []
    for line in text.replace("\xa0", " ").splitlines():
        found = _LINE.match(line)
        if found is None:
            continue
        label = " ".join(found.group(1).split()).casefold()
        if label not in labels:
            continue
        value = found.group(2)
        if label in values and values[label].casefold() != value.casefold():
            problems.append(
                f'"{found.group(1).strip()}" is written twice, as "{values[label]}" and'
                f' "{value}"; keep one'
            )
            continue
        values[label] = value
    return values, problems


def read_date(value: str) -> date | None:
    for layout in _DATE_FORMATS:
        try:
            return datetime.strptime(value.strip(), layout).date()
        except ValueError:
            continue
    return None


def _addresses(value: str) -> tuple[str, ...]:
    return tuple(part for part in re.split(r"[,;\s]+", value) if part)


@dataclass(frozen=True)
class CustomerSetup:
    """What the customer's Notes box says. Blank invoice code means missing."""

    invoice_code: str = ""
    delivery: Delivery = Delivery.EMAIL
    cc_emails: tuple[str, ...] = ()
    problems: list[str] = field(default_factory=list)


def customer_setup(notes: str) -> CustomerSetup:
    values, problems = labelled_lines(notes, CUSTOMER_LABELS)
    code = values.get("invoice code", "").strip().upper()
    if not code:
        problems.append('no "Invoice code:" line, so its invoices cannot be numbered')
    elif not re.fullmatch(r"[A-Z]{2}", code):
        problems.append(f'"Invoice code: {values["invoice code"]}" should be two letters, like MT')
        code = ""
    delivery = Delivery.EMAIL
    if "delivery" in values:
        wanted = values["delivery"].strip().casefold()
        if wanted in (Delivery.EMAIL.value, Delivery.PORTAL.value):
            delivery = Delivery(wanted)
        else:
            problems.append(f'"Delivery: {values["delivery"]}" should be email or portal')
    cc = _addresses(values.get("cc", ""))
    for address in cc:
        if "@" not in address:
            problems.append(f'"CC: {values["cc"]}" has something that is not an email address')
            cc = ()
            break
    return CustomerSetup(invoice_code=code, delivery=delivery, cc_emails=cc, problems=problems)


@dataclass(frozen=True)
class ProductSetup:
    """What the product's purchase description says. No start means missing."""

    start: date | None = None
    schedule: BillingSchedule = BillingSchedule.MONTHLY
    first_period: date | None = None
    send_automatically: bool = False
    problems: list[str] = field(default_factory=list)


def product_setup(description: str) -> ProductSetup:
    values, problems = labelled_lines(description, PRODUCT_LABELS)
    start = None
    if "start" not in values:
        problems.append('no "Start:" line, so I do not know which month to expect first')
    else:
        start = read_date(values["start"])
        if start is None:
            problems.append(f'"Start: {values["start"]}" should be a date, like 2026-02-01')
    schedule = BillingSchedule.MONTHLY
    if values.get("schedule", "").strip():
        wanted = " ".join(values["schedule"].split()).casefold()
        if wanted in _SCHEDULES:
            schedule = _SCHEDULES[wanted]
        else:
            problems.append(
                f'"Schedule: {values["schedule"]}" should be one of: ' + ", ".join(_SCHEDULES)
            )
    first_period = None
    if "first period" in values:
        first_period = read_date(values["first period"])
        if first_period is None:
            problems.append(
                f'"First period: {values["first period"]}" should be a date, like 2026-02-02'
            )
    elif schedule in (BillingSchedule.WEEKLY, BillingSchedule.EVERY_TWO_WEEKS):
        problems.append(
            f'a {schedule.value} schedule needs a "First period:" line with the first day'
            " of any one period"
        )
    automatic = False
    if "send automatically" in values:
        answer = values["send automatically"].strip().casefold()
        if answer in ("yes", "no"):
            automatic = answer == "yes"
        else:
            problems.append(
                f'"Send automatically: {values["send automatically"]}" should be yes or no'
            )
    return ProductSetup(
        start=start,
        schedule=schedule,
        first_period=first_period,
        send_automatically=automatic,
        problems=problems,
    )
