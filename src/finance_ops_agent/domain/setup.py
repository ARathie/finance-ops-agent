"""Setting up a new consultant, client or engagement in QuickBooks from Kevin's
email (decision 56).

When an email or a timesheet cannot be placed, the question Kevin gets includes
a short form. He copies it into his reply and fills it in. This module is the
form and the reading of it, and it is code, never the model: the two rates are
on it, and CLAUDE.md rule 7 keeps money away from the model.

Reading it is deliberately strict. Every line is `Label: value`; a label the
form does not have is ignored; a value that does not read cleanly is a problem
named back to Kevin, never a guess. Nothing here creates anything: the result
is a plan that Kevin confirms, with a one-time code, before the agent touches
QuickBooks.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime

from finance_ops_agent.domain import checks
from finance_ops_agent.domain.engagements import EngagementWorkbook
from finance_ops_agent.domain.money import Money

# The labels, in the order the form shows them. The key is how the plan refers
# to each; the text is exactly what Kevin sees and writes.
CONSULTANT = "Consultant"
CONSULTANT_EMAIL = "Consultant email"
FIRM = "Paid through firm"
PAY_WITHIN = "Icon pays within (days)"
CLIENT = "Client"
CLIENT_LEGAL = "Client legal name"
CLIENT_EMAIL = "Client billing email"
CLIENT_PAYS_WITHIN = "Client pays within (days)"
INVOICE_CODE = "Invoice code"
BILL_RATE = "Bill rate"
PAY_RATE = "Pay rate"
START = "Start date"

LABELS = (
    CONSULTANT,
    CONSULTANT_EMAIL,
    FIRM,
    PAY_WITHIN,
    CLIENT,
    CLIENT_LEGAL,
    CLIENT_EMAIL,
    CLIENT_PAYS_WITHIN,
    INVOICE_CODE,
    BILL_RATE,
    PAY_RATE,
    START,
)
_HINTS = {
    FIRM: "leave blank if Icon pays the consultant directly",
    CLIENT_LEGAL: "new client only; blank means the same as Client",
    CLIENT_EMAIL: "new client only",
    CLIENT_PAYS_WITHIN: "new client only",
    INVOICE_CODE: "new client only; two letters, as in 083126AC-PS",
    BILL_RATE: "per hour, what the client is charged",
    PAY_RATE: "per hour, what Icon pays",
    START: "first day of the engagement, e.g. 2026-09-01",
}

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_CODE = re.compile(r"^[A-Za-z]{2}$")
# Where a reply stops being Kevin's own words: the quoted email below it.
_QUOTE_START = re.compile(
    r"^\s*(On .+wrote:\s*$|-{2,}\s*Original Message\s*-{2,}|From:\s|Sent from my )",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class EngagementSetup:
    """Everything needed to set one engagement up, read and checked."""

    consultant: str
    consultant_email: str
    firm: str  # who Icon pays, when it is not the consultant
    pay_within_days: int
    client: str
    new_client: bool
    client_legal_name: str  # new client only
    client_email: str  # new client only
    client_pays_within_days: int  # new client only; 0 for an existing one
    invoice_code: str  # new client only
    bill_rate: Money
    pay_rate: Money
    start: date

    def summary_lines(self) -> list[str]:
        """What will be created or reused, in Kevin's words."""
        payee = self.firm or self.consultant
        lines = []
        if self.new_client:
            lines.append(
                f"New client {self.client} ({self.client_legal_name}): invoices to"
                f" {self.client_email}, {self.client_pays_within_days} days to pay,"
                f" invoice code {self.invoice_code}."
            )
        else:
            lines.append(f"Existing client {self.client}.")
        lines += [
            f"Consultant {self.consultant}, timesheets from {self.consultant_email}.",
            f"Icon pays {payee} within {self.pay_within_days} days.",
            f"Engagement {self.client}:{self.consultant}, starting {self.start.isoformat()}.",
            f"Bill rate ${self.bill_rate} an hour (what {self.client} is charged).",
            f"Pay rate ${self.pay_rate} an hour (what Icon pays {payee}).",
        ]
        if self.pay_rate.cents > self.bill_rate.cents:
            lines.append("Note: Icon would pay more an hour than it charges.")
        return lines

    def to_dict(self) -> dict[str, object]:
        return {
            "consultant": self.consultant,
            "consultant_email": self.consultant_email,
            "firm": self.firm,
            "pay_within_days": self.pay_within_days,
            "client": self.client,
            "new_client": self.new_client,
            "client_legal_name": self.client_legal_name,
            "client_email": self.client_email,
            "client_pays_within_days": self.client_pays_within_days,
            "invoice_code": self.invoice_code,
            "bill_rate_cents": self.bill_rate.cents,
            "pay_rate_cents": self.pay_rate.cents,
            "start": self.start.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "EngagementSetup":
        return cls(
            consultant=str(data["consultant"]),
            consultant_email=str(data["consultant_email"]),
            firm=str(data["firm"]),
            pay_within_days=int(str(data["pay_within_days"])),
            client=str(data["client"]),
            new_client=bool(data["new_client"]),
            client_legal_name=str(data["client_legal_name"]),
            client_email=str(data["client_email"]),
            client_pays_within_days=int(str(data["client_pays_within_days"])),
            invoice_code=str(data["invoice_code"]),
            bill_rate=Money(int(str(data["bill_rate_cents"]))),
            pay_rate=Money(int(str(data["pay_rate_cents"]))),
            start=date.fromisoformat(str(data["start"])),
        )


def form(prefill: dict[str, str]) -> list[str]:
    """The lines Kevin copies into his reply, with what is already known."""
    lines = []
    for label in LABELS:
        value = prefill.get(label, "")
        hint = _HINTS.get(label)
        lines.append(f"{label}: {value}" + (f"    ({hint})" if hint and not value else ""))
    return lines


def _own_words(reply: str) -> list[str]:
    lines: list[str] = []
    for line in reply.splitlines():
        if _QUOTE_START.match(line):
            break
        if line.lstrip().startswith(">"):
            continue
        lines.append(line)
    return lines


def filled_in(reply: str) -> dict[str, str]:
    """The form's labelled lines in Kevin's own part of the reply.

    A hint left in brackets is not part of the value; an empty value is
    returned as empty, which is different from the label being absent."""
    found: dict[str, str] = {}
    wanted = {label.casefold(): label for label in LABELS}
    for line in _own_words(reply):
        label, sep, value = line.partition(":")
        key = wanted.get(label.strip().strip("*-• ").casefold())
        if not sep or key is None:
            continue
        value = re.sub(r"\s*\((?:[^()]*)\)\s*$", "", value).strip()
        found[key] = value
    return found


def looks_like_a_setup(reply: str) -> bool:
    """Kevin filled in the form rather than answering in words: the two rates
    are what only a setup reply carries."""
    fields = filled_in(reply)
    return BILL_RATE in fields and PAY_RATE in fields


def _days(text: str) -> int | None:
    match = re.fullmatch(r"(?:net\s*)?(\d{1,3})(?:\s*days?)?", text.strip(), re.IGNORECASE)
    return int(match.group(1)) if match else None


def _date(text: str) -> date | None:
    for shape in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(text.strip(), shape).date()
        except ValueError:
            continue
    return None


def _rate(text: str) -> Money | None:
    cleaned = re.sub(r"\s*(?:/\s*(?:hr|hour)|an hour|per hour)\s*$", "", text.strip(), flags=re.I)
    try:
        rate = Money.parse(cleaned)
    except ValueError:
        return None
    return rate if rate.cents > 0 else None


def read_setup(
    reply: str, workbook: EngagementWorkbook
) -> tuple[EngagementSetup | None, list[str], dict[str, str]]:
    """The plan in Kevin's reply, or what is wrong with it.

    Returns the plan (None when anything is wrong), the problems in plain
    words, and the values as he gave them, so asking again can show him his
    own answers rather than a blank form."""
    fields = filled_in(reply)
    problems: list[str] = []

    def need(label: str) -> str:
        value = fields.get(label, "").strip()
        if not value:
            problems.append(f'"{label}" is blank.')
        return value

    consultant = need(CONSULTANT)
    consultant_email = need(CONSULTANT_EMAIL)
    if consultant_email and not _EMAIL.match(consultant_email):
        problems.append(f'"{consultant_email}" is not an email address.')
    firm = fields.get(FIRM, "").strip()
    pay_within = _days(fields.get(PAY_WITHIN, "")) if fields.get(PAY_WITHIN) else None
    if pay_within is None:
        problems.append(f'"{PAY_WITHIN}" should be a number of days, like 15.')

    client_text = need(CLIENT)
    existing = [c for c in workbook.clients if checks.names_match(c.name, client_text)]
    new_client = client_text != "" and not existing
    client = existing[0].name if existing else client_text
    legal = email = code = ""
    client_days = 0
    if new_client:
        legal = fields.get(CLIENT_LEGAL, "").strip() or client
        email = need(CLIENT_EMAIL)
        if email and not all(_EMAIL.match(part) for part in checks.split_addresses(email)):
            problems.append(f'"{email}" is not an email address.')
        found_days = _days(fields.get(CLIENT_PAYS_WITHIN, ""))
        if found_days is None:
            problems.append(
                f'{client} is a new client, so "{CLIENT_PAYS_WITHIN}" should be a'
                " number of days, like 30."
            )
        client_days = found_days or 0
        code = need(INVOICE_CODE).upper()
        if code and not _CODE.match(code):
            problems.append(f'The invoice code should be two letters, not "{code}".')
        taken = [c.name for c in workbook.clients if c.invoice_code.upper() == code]
        if code and taken:
            problems.append(f"The invoice code {code} is already {taken[0]}'s.")

    bill = _rate(fields.get(BILL_RATE, ""))
    if bill is None:
        problems.append(f'"{BILL_RATE}" should be an amount an hour, like 140.00.')
    pay = _rate(fields.get(PAY_RATE, ""))
    if pay is None:
        problems.append(f'"{PAY_RATE}" should be an amount an hour, like 100.00.')
    start = _date(fields.get(START, ""))
    if start is None:
        problems.append(f'"{START}" should be a date, like 2026-09-01.')

    if (
        consultant
        and client
        and any(
            checks.names_match(row.consultant, consultant)
            and checks.names_match(row.client, client)
            for row in workbook.engagements
        )
    ):
        problems.append(
            f"{consultant} already has an engagement at {client} in QuickBooks; nothing"
            ' needs setting up. Reply "try again" instead.'
        )
    if problems or pay_within is None or bill is None or pay is None or start is None:
        return None, problems, fields
    return (
        EngagementSetup(
            consultant=consultant,
            consultant_email=consultant_email,
            firm=firm,
            pay_within_days=pay_within,
            client=client,
            new_client=new_client,
            client_legal_name=legal,
            client_email=email,
            client_pays_within_days=client_days,
            invoice_code=code,
            bill_rate=bill,
            pay_rate=pay,
            start=start,
        ),
        [],
        fields,
    )
