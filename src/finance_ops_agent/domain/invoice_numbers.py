"""The invoice number Kevin uses: `<MMDDYY><client code>-<consultant code>`.

The date is the **end of the billing period**, not the day the invoice was
made, so August's invoice reads `083126` however late it goes out. The client
code is the two letters on the client's row (`MT` for Mastec, `IS` for
iStream); the agent never invents one, because no rule derives them from the
names. The consultant code is their initials -- `PS` for Priya Shah -- so a
whole number reads `083126MT-PS`.

Two consultants at one client can share initials. When they do, both of them
get the first initial and the whole last name instead (`PSHAH` and `PSINGH`),
so neither number is ambiguous and neither is a special case. The swap happens
only where the clash is: Priya Shah stays `PS` at every other client.

One invoice per consultant per client per month (CLAUDE.md rule 3) means the
number is unique on its own. A correction is the exception: the replacement
invoice covers the same period as the one it replaces, so it takes a `-2`
suffix, and the next a `-3`. `application/outgoing.py` asks the store which
numbers are already spoken for.

QuickBooks Online allows 21 characters in `DocNumber`, so a very long last name
is shortened to fit rather than being refused at the point of invoicing.
"""

from collections.abc import Mapping
from datetime import date

MAX_LENGTH = 21  # QuickBooks Online's limit for DocNumber
VOID_MARKER = "-VOID"
CLIENT_CODE_LENGTH = 2


def _letters(text: str) -> str:
    """Just the letters, upper case: O'Brien and Smith-Jones stay one word."""
    return "".join(character for character in text if character.isalpha()).upper()


def initials(name: str) -> str | None:
    """`Priya Shah` -> `PS`. None when the name is not two or more words, which
    becomes a review rather than a guess."""
    words = [word for word in name.split() if _letters(word)]
    if len(words) < 2:
        return None
    return _letters(words[0])[:1] + _letters(words[-1])[:1]


def with_last_name(name: str) -> str | None:
    """`Priya Shah` -> `PSHAH`, for when initials alone would be ambiguous."""
    words = [word for word in name.split() if _letters(word)]
    if len(words) < 2:
        return None
    return _letters(words[0])[:1] + _letters(words[-1])


def codes_for_client(consultants: Mapping[str, str]) -> dict[str, str]:
    """The consultant code for everyone working at one client.

    `consultants` maps each consultant's full name to the "Initials" cell on
    their row, blank where Kevin has not filled one in. A name whose code
    cannot be worked out is left out, and the caller raises a review.
    """
    derived: dict[str, str] = {}
    for name, override in consultants.items():
        chosen = _letters(override) or initials(name)
        if chosen:
            derived[name] = chosen

    shared = {code for code in derived.values() if list(derived.values()).count(code) > 1}
    if not shared:
        return derived

    # Both of the consultants who clash get the longer form, not just the
    # newcomer: two numbers that differ only in which one Kevin saw first would
    # be worse than either.
    resolved: dict[str, str] = {}
    for name, code in derived.items():
        if code not in shared or _letters(consultants.get(name, "")):
            resolved[name] = code  # an explicit override is Kevin's word, kept
            continue
        resolved[name] = with_last_name(name) or code
    return resolved


def voided_number(number: str, attempt: int = 1) -> str:
    """What a cancelled invoice is renamed to, so its number comes free.

    QuickBooks will not let a second invoice take a number another invoice
    already has, even a voided one. Renaming the voided invoice to
    `083126MT-PS-VOID` before voiding it hands the real number back, so the
    replacement is `083126MT-PS` again rather than `083126MT-PS-2` -- the
    number says which month's work it is for, and a correction should not
    change that.
    """
    suffix = VOID_MARKER if attempt <= 1 else f"{VOID_MARKER}{attempt}"
    return f"{number[: MAX_LENGTH - len(suffix)]}{suffix}"


def is_voided_number(number: str) -> bool:
    """Ours, and already cancelled: never to be mistaken for a live invoice."""
    base, _, tail = number.rpartition("-")
    return bool(base) and tail.startswith("VOID")


def invoice_number(
    period_end: date, client_code: str, consultant_code: str, attempt: int = 1
) -> str:
    """`083126MT-PS`, and `083126MT-PS-2` for the invoice that replaces it."""
    suffix = "" if attempt <= 1 else f"-{attempt}"
    stamp = period_end.strftime("%m%d%y")
    room = MAX_LENGTH - len(stamp) - len(client_code) - 1 - len(suffix)
    return f"{stamp}{client_code.upper()}-{consultant_code[:room]}{suffix}"
