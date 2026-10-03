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
