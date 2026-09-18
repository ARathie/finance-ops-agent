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

from finance_ops_agent.adapters.email.client import (
    Folders,
    MailAccount,
    MailboxProblem,
    open_imap,
    open_smtp,
)
from finance_ops_agent.adapters.email.inbox import INBOX
from finance_ops_agent.domain.money import Money


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


def check_engagement_list(load: Callable[[], tuple[int, list[str]]], path: Path) -> Check:
    """Say which file was read, not only what was in it.

    `FOPS_ENGAGEMENT_LIST` is usually a relative path, so the file depends on
    the folder the command was run in, and a copy edited somewhere else looks
    exactly like a change that did not take. The resolved path settles it.
    """

    def run() -> str:
        rows, problems = load()
        where = f"{rows} engagement row(s) from {path.resolve()}"
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
            raise RuntimeError("QuickBooks cannot price these consultants. " + " ".join(problems))
        return f"all {len(names)} consultant(s) have a product with a rate"

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
