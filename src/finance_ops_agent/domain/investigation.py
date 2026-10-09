"""What the investigator is given and what it gives back (docs/decisions.md #61).

The investigator looks into one stuck item with read-only tools and comes back
with what it found and what Kevin could do. It decides nothing: its proposals
go into the review email, Kevin picks one by replying, and his reply goes
through the same checks as any other (decision 59).
"""

import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from finance_ops_agent.domain.review import RATES_UNCONFIRMED


@dataclass(frozen=True)
class ToolSpec:
    """One tool the investigator may call, described for the model."""

    name: str
    description: str
    input_schema: dict[str, Any]


class Proposal(BaseModel):
    """One way out, as Kevin would read it."""

    model_config = ConfigDict(frozen=True)

    # What to do, in plain words, including anything done outside email
    # ("Delete invoice 083126MT-MK in QuickBooks, then reply 'try again'").
    what_to_do: str
    # The exact words Kevin can reply to choose it ("try again", "use
    # 083126MT-MK-revised", "ignore"). Empty when it is done outside email.
    reply_to_choose: str = ""
    why: str = ""  # one line: when this is the right choice


class Investigation(BaseModel):
    """The investigator's answer. Plain words; never instructions to code."""

    model_config = ConfigDict(frozen=True)

    found: str  # what is wrong and why, in two or three sentences
    evidence: list[str] = Field(default_factory=list)  # facts from the tools it relied on
    proposals: list[Proposal] = Field(default_factory=list)  # best first; at most three
    sure: bool = False  # True only when the evidence settles the cause


@dataclass(frozen=True)
class ToolCall:
    """One call the investigator made, kept with its answer for the record."""

    name: str
    arguments: dict[str, Any]
    ok: bool


@dataclass(frozen=True)
class InvestigationResult:
    investigation: Investigation
    calls: tuple[ToolCall, ...]


_MONEY = re.compile(r"\$\s?-?[\d,]+(?:\.\d+)?")


def without_money(text: str) -> str:
    """Every dollar amount masked. Rates and amounts are never sent to the model
    (docs/technical-design.md, security), and the investigator never needs one:
    whose invoice holds a number is not a question about how much it is for."""
    return _MONEY.sub("$[amount]", text)


# Markup in a field means the model wrote its answer as text instead of filling
# the form (the first live run put every option inside `found` as tags). A
# placeholder Kevin fills in, such as "<name>", is not markup.
_MARKUP = re.compile(
    r"</[\w-]+>|<(?:found|evidence|proposals|item|what_to_do|reply_to_choose|why|sure)\b"
)

# Where "try again" means something: an invoice or an email that failed and can
# simply be attempted again once the cause is fixed. Anywhere else it does
# nothing, so it is never offered or accepted there.
RETRYABLE_REVIEWS = frozenset({"QUICKBOOKS_FAILED", "SEND_FAILED"})


def retries_on_try_again(code: str, message: str) -> bool:
    """Whether "try again" on this question attempts something again now.

    Only a failed invoice or a failed send. Not a question waiting on
    QuickBooks for an item's rates, though it carries the same code: that one
    closes by itself once QuickBooks answers, and closing it early would let
    the item be invoiced on figures nobody confirmed (decisions 55 and 61)."""
    return code in RETRYABLE_REVIEWS and not message.startswith(RATES_UNCONFIRMED)


def problems_with_answer(investigation: Investigation) -> list[str]:
    """Why an answer cannot go into Kevin's email as it is. Empty means usable."""
    found: list[str] = []
    if not investigation.found.strip():
        found.append("`found` is empty")
    texts = [investigation.found, *investigation.evidence]
    for proposal in investigation.proposals:
        texts += [proposal.what_to_do, proposal.why]
    if any(_MARKUP.search(text) for text in texts):
        found.append("a field holds tags or markup; every field must be plain sentences")
    if not investigation.proposals:
        found.append("there are no proposals; give at least one way out")
    return found


# --- what Kevin can reply to one email (decision 67) ---


@dataclass(frozen=True)
class ReplyForm:
    """One kind of reply the agent understands, as the investigator is shown it
    ("use <N> hours") and as code recognises it."""

    shown: str
    pattern: re.Pattern[str]

    def matches(self, reply: str) -> bool:
        return self.pattern.fullmatch(reply) is not None


TRY_AGAIN = ReplyForm("try again", re.compile(r"try again"))
USE_NUMBER = ReplyForm(
    "use <invoice number, spelled out in full>",
    re.compile(r"use (?!the new one$)(?!\d+(?:\.\d+)? hours$)\S+"),
)
USE_HOURS = ReplyForm("use <N> hours", re.compile(r"use \d+(?:\.\d+)? hours"))
THIS_IS_FOR = ReplyForm("this is for <client>", re.compile(r"this is for .+"))
CONSULTANT_IS = ReplyForm("the consultant is <name>", re.compile(r"the consultant is .+"))
PERIOD_IS = ReplyForm("the period is <start> to <end>", re.compile(r"the period is .+"))
APPROVED_BY = ReplyForm("approved by <name> on <date>", re.compile(r"approved by .+"))
USE_NEW_ONE = ReplyForm("use the new one", re.compile(r"use the new one"))
SHOW_ME_FIRST = ReplyForm("show me first", re.compile(r"show me first"))
IGNORE = ReplyForm("ignore", re.compile(r"ignore"))
FROM = ReplyForm("this is from <consultant>", re.compile(r"this is from .+"))

# The facts a review about a timesheet can be answered with.
_TIMESHEET_FACTS = (THIS_IS_FOR, CONSULTANT_IS, PERIOD_IS, USE_HOURS, APPROVED_BY)


def reply_text(text: str) -> str:
    """A reply as compared: lower case, outer quotes and end punctuation off."""
    return text.strip().strip("\"'").rstrip(".!").strip().casefold()


def replies_understood(
    reviews: list[tuple[str, str]], *, set_aside: bool = False, list_problems: bool = False
) -> list[ReplyForm]:
    """What a reply to this one email can say and have the agent act on it.

    Worked out by code from what the email is about, and shown to the
    investigator, so it never has to guess which of the agent's words apply
    here -- and checked again before anything it offers reaches Kevin. A
    reply outside this list is turned down, or does nothing (decisions 59, 61,
    55 and 66). `reviews` is (code, message) for each question the email asks.
    """
    codes = {code for code, _message in reviews}
    if list_problems:
        return []  # a reply cannot fix a row: the list is fixed in the list
    if set_aside:
        # Decision 55's replies to an email that could not be placed. Naming
        # the consultant helps only where the consultant is the question.
        forms = [TRY_AGAIN]
        if codes & {"UNKNOWN_SENDER", "CONSULTANT_UNKNOWN"}:
            forms.append(FROM)
        return [*forms, IGNORE]
    if not codes or codes & {"NO_ATTACHMENT", "CANT_READ_ATTACHMENT"}:
        # No timesheet behind it: an answer has nothing to apply to (decision 66).
        return [IGNORE]
    forms = list(_TIMESHEET_FACTS)
    if "CORRECTION" in codes:
        forms.append(USE_NEW_ONE)
    if any(retries_on_try_again(code, message) for code, message in reviews):
        forms.append(TRY_AGAIN)
    if "QUICKBOOKS_FAILED" in codes and any(
        "invoice number" in message.casefold() or "numbered" in message.casefold()
        for _code, message in reviews
    ):
        forms.append(USE_NUMBER)
    return [*forms, SHOW_ME_FIRST, IGNORE]


def form_of(reply: str, forms: list[ReplyForm]) -> ReplyForm | None:
    """Which of these forms a reply is, or None for one the agent would not
    understand here."""
    said = reply_text(reply)
    return next((form for form in forms if form.matches(said)), None)
