"""`fops doctor`: check every credential and setting without touching a client.

The mailbox checks log in over IMAP and SMTP, look at the folders, and make
sure the credentials are the agent's own (a mailbox password reaches one
mailbox by construction, so there is nothing else to prove). The doctor lives
with the command line because every check is about a concrete adapter, and
`application/` never imports an adapter.
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from finance_ops_agent.adapters.email.client import (
    Folders,
    MailAccount,
    MailboxProblem,
    open_imap,
    open_smtp,
)
from finance_ops_agent.adapters.email.inbox import INBOX
from finance_ops_agent.domain.engagements import EngagementWorkbook
from finance_ops_agent.domain.money import Money
from finance_ops_agent.ports.accounting import AccountingParty


class CheckResult(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"


@dataclass(frozen=True)
class Check:
    name: str
    result: CheckResult
    detail: str

    def line(self) -> str:
        mark = {CheckResult.PASS: "ok  ", CheckResult.FAIL: "FAIL", CheckResult.SKIP: "--  "}
        return f"{mark[self.result]} {self.name}: {self.detail}"


# Long enough for a check that names one line per engagement, since silently
# dropping the last few would hide exactly what someone needs to fix.
MAX_DETAIL = 4000


# What Kevin has to have set up in QuickBooks, in his words. Every QuickBooks
# check here is the machine-readable half of a section in that document, so the
# two are changed together (CLAUDE.md, definition of done).
SETUP_DOC = "docs/quickbooks-setup.md"


def setup_pointer(failures: list[Check]) -> str | None:
    """Where to go and fix a QuickBooks check, said once.

    Each message already says what is wrong; none of them says where the thing
    to change lives, and repeating that inside a dozen messages would only make
    them longer to read.
    """
    if not any(check.name.startswith("quickbooks") for check in failures):
        return None
    return f"What QuickBooks needs to have in it: {SETUP_DOC}"


def _run(name: str, check: Callable[[], str]) -> Check:
    try:
        return Check(name, CheckResult.PASS, check())
    except Exception as error:  # a doctor reports, it never crashes
        said = str(error)
        if len(said) > MAX_DETAIL:
            said = said[:MAX_DETAIL] + " …and more, cut short here"
        return Check(name, CheckResult.FAIL, said)


def check_mailbox_is_the_agents(account: MailAccount, agent_mailbox: str) -> Check:
    """The credentials must be the agent's own mailbox, never Kevin's."""
    name = "mailbox credentials"
    if account.username.strip().casefold() != agent_mailbox.strip().casefold():
        return Check(
            name,
            CheckResult.FAIL,
            f"MAIL_USERNAME is {account.username} but FOPS_AGENT_MAILBOX is {agent_mailbox};"
            " the agent must log in to its own mailbox",
        )
    plain = [s for s in (account.imap_security, account.smtp_security) if s == "none"]
    detail = f"{account.username} via {account.imap_host}:{account.imap_port} (imap)"
    detail += f" and {account.smtp_host}:{account.smtp_port} (smtp)"
    if plain:
        detail += "; PLAIN TEXT, test server only"
    return Check(name, CheckResult.PASS, detail)


def check_imap_login(account: MailAccount) -> Check:
    def run() -> str:
        client = open_imap(account)
        try:
            info = client.select_folder(INBOX, readonly=True)
            return f"logged in; {int(info[b'EXISTS'])} message(s) in the inbox"
        finally:
            client.logout()

    return _run("read the mailbox (imap)", run)


def check_agent_folders(account: MailAccount, names: tuple[str, ...]) -> Check:
    def run() -> str:
        client = open_imap(account)
        try:
            folders = Folders(client, account.folder_prefix)
            made = [folders.agent_folder(name) for name in names]
            return "folders ready: " + ", ".join(made)
        finally:
            client.logout()

    return _run("agent folders", run)


def check_sent_folder(account: MailAccount) -> Check:
    def run() -> str:
        client = open_imap(account)
        try:
            return "sent copies go to " + Folders(client, account.folder_prefix).sent_folder(
                account.sent_folder
            )
        finally:
            client.logout()

    return _run("sent folder", run)


def check_smtp_login(account: MailAccount) -> Check:
    def run() -> str:
        server = open_smtp(account)
        with server:
            return f"logged in to {account.smtp_host}:{account.smtp_port}"

    return _run("send mail (smtp)", run)


def check_timesheet_forwarders(forwarders: tuple[str, ...], mode: str) -> Check:
    """Say, loudly, when an address may send in someone else's timesheet.

    This exists for the first-cycle test, where old timesheets are forwarded by
    hand instead of arriving from the consultants themselves (decision 25). It
    is the one setting that widens who the agent will take a timesheet from, so
    it is never silent, and outside dry run it is a warning rather than a note.
    """
    name = "timesheet forwarders"
    if not forwarders:
        return Check(
            name,
            CheckResult.SKIP,
            "none; timesheets count only from the addresses on the engagement list",
        )
    listed = ", ".join(forwarders)
    if mode == "dry_run":
        return Check(
            name, CheckResult.PASS, f"{listed} may forward someone else's timesheet (testing)"
        )
    return Check(
        name,
        CheckResult.FAIL,
        f"{listed} may forward someone else's timesheet, and the mode is {mode}, not dry_run;"
        " clear FOPS_TIMESHEET_FORWARDERS before the agent sends anything for real",
    )


def check_engagement_list(
    load: Callable[[], tuple[int, list[str]]], path: Path, stored: bool = False
) -> Check:
    """Say where it was read from, not only what was in it.

    `FOPS_ENGAGEMENT_LIST` is usually a relative path, so the file depends on
    the folder the command was run in, and a copy edited somewhere else looks
    exactly like a change that did not take. The resolved path settles it.

    Once the list has been imported the agent reads its own store and does not
    open that file at all (decision 40), and someone editing the workbook and
    seeing nothing change deserves to be told so by name.
    """

    def run() -> str:
        rows, problems = load()
        source = "the agent's own store" if stored else str(path.resolve())
        where = f"{rows} engagement row(s) from {source}"
        if problems:
            raise MailboxProblem(f"{where}, but {len(problems)} problem(s): {problems[0]}")
        return f"{where}, no problems"

    return _run("engagement list", run)


def check_database(describe: Callable[[], str]) -> Check:
    return _run("database", describe)


def check_quickbooks_tokens(store: object) -> Check:
    """Are we connected, and is the refresh token still healthy?"""
    from finance_ops_agent.adapters.quickbooks.tokens import (
        NotConnected,
        TokenStore,
        utcnow,
    )

    name = "quickbooks connection"
    assert isinstance(store, TokenStore)
    try:
        tokens = store.load()
    except NotConnected as error:
        return Check(name, CheckResult.FAIL, str(error))
    warning = tokens.refresh_token_warning(utcnow())
    if warning:
        return Check(name, CheckResult.FAIL, warning)
    return Check(
        name,
        CheckResult.PASS,
        f"connected to the {tokens.environment} company {tokens.realm_id},"
        f" refreshed {tokens.days_since_refresh(utcnow())} day(s) ago",
    )


def check_quickbooks_customers(accounting: object, wanted: Callable[[], list[str]]) -> Check:
    """Every client the agent may invoice must already exist in QuickBooks:
    the agent never creates customers."""
    from finance_ops_agent.adapters.quickbooks.online import QuickBooksOnline

    name = "quickbooks customers"
    assert isinstance(accounting, QuickBooksOnline)

    def run() -> str:
        names = wanted()
        problems: list[str] = []
        for customer in names:
            try:
                accounting.customer_ref(customer)
            except Exception as error:
                # The reason matters: "spelled differently", "there are two of
                # them", and "you are connected to the wrong company" all look
                # the same once it is flattened to "missing".
                problems.append(f"{customer}: {error}")
        if problems:
            raise RuntimeError(
                f"I looked in {accounting.company} and could not use"
                f" {len(problems)} of {len(names)} client name(s). " + " ".join(problems)
            )
        return f"all {len(names)} client name(s) exist in {accounting.company}"

    return _run(name, run)


@dataclass(frozen=True)
class ExpectedProduct:
    """One engagement, as the engagement list has it, for checking against
    what QuickBooks holds."""

    consultant: str
    client: str
    clients: list[str]  # the names that client might be filed under
    bill_rate_cents: int
    pay_rate_cents: int
    payee: str


def check_quickbooks_products(
    accounting: object, wanted: Callable[[], list[ExpectedProduct]]
) -> Check:
    """Every engagement the agent may invoice must have a product in
    QuickBooks, under a category named for the client, with the rate on it:
    that rate is what is billed (docs/decisions.md #30 and #36)."""
    from finance_ops_agent.adapters.quickbooks.online import QuickBooksOnline

    name = "quickbooks products"
    assert isinstance(accounting, QuickBooksOnline)

    def run() -> str:
        names = wanted()
        problems: list[str] = []
        for expected in names:
            try:
                product = accounting.product_for(expected.consultant, expected.clients)
            except Exception as error:
                problems.append(f"{expected.consultant}: {error}")
                continue
            # A bill rate that disagrees is not a warning: the invoice would be
            # made, found to disagree, and voided (decision 30).
            if product.unit_price_cents != expected.bill_rate_cents:
                problems.append(
                    f"{expected.consultant} at {expected.client}:"
                    f" {product.name} bills ${Money(product.unit_price_cents)} an hour"
                    f" and the engagement list says ${Money(expected.bill_rate_cents)}."
                )
        if problems:
            raise RuntimeError(
                f"I looked in {accounting.company} and cannot price"
                f" {len(problems)} of {len(names)} engagement(s). " + " ".join(problems)
            )
        return (
            f"all {len(names)} engagement(s) have a product in {accounting.company},"
            " charging what the engagement list says"
        )

    return _run(name, run)


def check_quickbooks_engagements(
    accounting: object, load: Callable[[], "EngagementWorkbook"]
) -> Check:
    """Which engagements does QuickBooks say are live, and can each be scheduled?

    The products check asks the question one way round -- does every row on the
    engagement list have a product? This asks it the other way, which is the way
    that matters now that QuickBooks is what says an engagement is live
    (docs/decisions.md #42): an engagement QuickBooks has and the list has no
    row for cannot be scheduled, because the billing schedule and the start date
    are still the workbook's to say.

    Both halves of a disagreement are named, because they are fixed in different
    places: a missing row is fixed in the list, and a row the agent will stop
    expecting timesheets for is fixed by making its product active again -- or
    is correct, and the engagement has finished.
    """
    from finance_ops_agent.adapters.quickbooks.online import QuickBooksOnline
    from finance_ops_agent.domain.live_engagements import live_engagements

    name = "quickbooks engagements"
    assert isinstance(accounting, QuickBooksOnline)

    def run() -> str:
        workbook = load()
        listed = accounting.engagements()
        answer = live_engagements(workbook, [(one.consultant, one.client) for one in listed])
        rateless = sorted(
            f"{one.consultant} at {one.client}" for one in listed if one.bill_rate_cents is None
        )
        problems: list[str] = []
        if answer.without_a_row:
            problems.append(
                f"{len(answer.without_a_row)} engagement(s) in {accounting.company} have no row"
                " on the engagement list, so I cannot tell how often to expect a timesheet"
                f" or when the period ends: {', '.join(answer.without_a_row)}."
            )
        if answer.finished:
            named = ", ".join(f"{consultant} at {client}" for consultant, client in answer.finished)
            problems.append(
                f"The engagement list still calls {len(answer.finished)} engagement(s) active"
                f" and {accounting.company} has no live product for them, so I will expect no"
                f" new periods: {named}. Make the product active again, or mark the row"
                " inactive if the engagement has finished."
            )
        if rateless:
            problems.append(
                f"{len(rateless)} product(s) have no rate, and the rate on the product is what"
                f" I bill: {', '.join(rateless)}."
            )
        if problems:
            raise RuntimeError(" ".join(problems))
        if not listed:
            return (
                f"{accounting.company} lists no products under a category, so the engagement"
                " list decides which engagements are live, as it did before"
            )
        return (
            f"{len(answer.live)} live engagement(s) in {accounting.company}, each with a row"
            " on the engagement list to schedule it from"
        )

    return _run(name, run)


def check_quickbooks_pay(accounting: object, wanted: Callable[[], list[ExpectedProduct]]) -> Check:
    """Does the purchase side of each product agree with the engagement list?

    What Icon pays, and who it pays, are still taken from the engagement list.
    This check reads what QuickBooks holds beside them, so the two can be made
    to agree before anything is moved across (docs/decisions.md #37). A product
    with nothing on its purchase side is not a disagreement -- it is one that
    has not been filled in.
    """
    from finance_ops_agent.adapters.quickbooks.online import QuickBooksOnline

    name = "quickbooks pay rates"
    assert isinstance(accounting, QuickBooksOnline)

    def run() -> str:
        expected_all = wanted()
        differences: list[str] = []
        empty = 0
        checked = 0
        for expected in expected_all:
            try:
                product = accounting.product_for(expected.consultant, expected.clients)
            except Exception:
                continue  # the products check reports this one
            if product.purchase_cost_cents is None and not product.vendor:
                empty += 1
                continue
            checked += 1
            if (
                product.purchase_cost_cents is not None
                and product.purchase_cost_cents != expected.pay_rate_cents
            ):
                differences.append(
                    f"{expected.consultant} at {expected.client}: QuickBooks pays"
                    f" ${Money(product.purchase_cost_cents)} an hour and the engagement"
                    f" list says ${Money(expected.pay_rate_cents)}."
                )
            if product.vendor and product.vendor != expected.payee:
                differences.append(
                    f"{expected.consultant} at {expected.client}: QuickBooks pays"
                    f" {product.vendor} and the engagement list says {expected.payee}."
                )
        if differences:
            raise RuntimeError(
                "QuickBooks and the engagement list do not agree about what Icon pays."
                " Nothing is paid from QuickBooks yet, so no payment instruction is"
                " wrong today, but these have to agree before anything moves across. "
                + " ".join(differences)
            )
        if not checked:
            return (
                f"nothing to compare yet: none of {empty} engagement(s) has a rate or a"
                " vendor on the purchase side of its product"
            )
        note = f"all {checked} engagement(s) with a purchase side agree with the engagement list"
        return note if not empty else f"{note}; {empty} not filled in yet"

    return _run(name, run)


@dataclass(frozen=True)
class ExpectedParty:
    """A client or a payee as the engagement list has it, for comparing with
    what QuickBooks holds about the same person or company."""

    what: str  # "client" or "payee", for the message
    name: str
    lookup: str  # the name or id to ask QuickBooks by
    emails: list[str]
    payment_terms_days: int


class PartyRecords(Protocol):
    """The two questions this check asks of an accounting system.

    A protocol rather than the adapter itself: the checks above take `object`
    and assert the concrete type, which means they can only be exercised
    through recorded HTTP. Saying what is actually needed costs nothing and
    lets the comparison be tested on its own.
    """

    @property
    def company(self) -> str: ...

    def customer(self, name: str) -> "AccountingParty | None": ...

    def payee(self, ref: str) -> "AccountingParty | None": ...


def check_quickbooks_contacts(
    accounting: PartyRecords, wanted: Callable[[], list[ExpectedParty]]
) -> Check:
    """Do QuickBooks' own customer and vendor records agree with the list?

    Read and compared, not used: the addresses a timesheet may arrive from and
    the terms that set a due date still come from the engagement list, and this
    is what has to agree before either moves across (docs/decisions.md #45).
    It is the same shape decision 37 used before the pay rate moved, for the
    same reason -- a difference found here is found while someone is looking at
    the engagement list, not when an invoice is due.

    A blank field in QuickBooks is not a disagreement. It is one that has not
    been filled in, and saying so is how Kevin knows what is left to do.
    """
    name = "quickbooks contacts"

    def run() -> str:
        differences: list[str] = []
        empty: list[str] = []
        compared = 0
        for party in wanted():
            try:
                held = (
                    accounting.customer(party.lookup)
                    if party.what == "client"
                    else accounting.payee(party.lookup)
                )
            except Exception as error:
                differences.append(f"{party.name}: {error}")
                continue
            if held is None:
                empty.append(f"{party.name} (no record in QuickBooks)")
                continue
            compared += 1
            if not held.email:
                empty.append(f"{party.name} (no email)")
            elif party.emails and held.email.casefold() not in [
                address.casefold() for address in party.emails
            ]:
                differences.append(
                    f"QuickBooks has {held.email} for {party.name} and the engagement"
                    f" list has {', '.join(party.emails)}."
                )
            if held.payment_terms_days is None:
                empty.append(f"{party.name} (no payment terms)")
            elif held.payment_terms_days != party.payment_terms_days:
                differences.append(
                    f"QuickBooks gives {party.name} {held.payment_terms_days} day(s) to"
                    f" pay and the engagement list says {party.payment_terms_days}."
                )
        if differences:
            raise RuntimeError(
                "QuickBooks and the engagement list do not agree about who to contact or"
                " when payment is due. Nothing uses QuickBooks' answer yet, so nothing is"
                " wrong today, but these have to agree before either moves across. "
                + " ".join(differences)
            )
        missing = ", ".join(sorted(set(empty)))
        if not compared:
            found = f"nothing to compare yet in {accounting.company}"
            return f"{found}; not filled in yet: {missing}" if missing else found
        note = f"all {compared} record(s) in {accounting.company} agree with the engagement list"
        return note if not missing else f"{note}; not filled in yet: {missing}"

    return _run(name, run)


# The agent's credentials arrive as ordinary environment variables, from `.env`
# via launchd, `env_file`, or systemd (docs/running-it.md). The Anthropic SDK
# reads either of these names itself.
CLAUDE_CREDENTIAL_NAMES = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


def _describe_claude_model(model: str) -> str:
    """Ask the API to describe the configured model.

    This proves the credentials work and that `FOPS_MODEL` names a model the
    account may use. It reads no timesheet and spends no tokens.
    """
    import os

    import anthropic

    if not any(os.environ.get(name, "").strip() for name in CLAUDE_CREDENTIAL_NAMES):
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set; put it in .env next to the MAIL_ settings"
        )
    try:
        found = anthropic.Anthropic().models.retrieve(model)
    except anthropic.AuthenticationError as error:
        raise RuntimeError(
            "the key was refused; check ANTHROPIC_API_KEY in .env, or make a new"
            " key at console.anthropic.com"
        ) from error
    except anthropic.NotFoundError as error:
        raise RuntimeError(
            f"this account cannot use {model!r}; check FOPS_MODEL in .env"
        ) from error
    return f"{found.display_name} ({model}) answers; timesheets can be read"


def check_claude_api(model: str, describe: Callable[[str], str] | None = None) -> Check:
    """The credentials that read timesheets (docs/integrations/claude-extraction.md)."""
    ask = describe or _describe_claude_model
    return _run("claude api", lambda: ask(model))
