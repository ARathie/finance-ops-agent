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

from finance_ops_agent.adapters.email.client import (
    Folders,
    MailAccount,
    MailboxProblem,
    open_imap,
    open_smtp,
)
from finance_ops_agent.adapters.email.inbox import INBOX


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


def _run(name: str, check: Callable[[], str]) -> Check:
    try:
        return Check(name, CheckResult.PASS, check())
    except Exception as error:  # a doctor reports, it never crashes
        return Check(name, CheckResult.FAIL, str(error)[:300])


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


def check_engagement_list(load: Callable[[], tuple[int, list[str]]]) -> Check:
    def run() -> str:
        rows, problems = load()
        if problems:
            raise MailboxProblem(
                f"{rows} engagement row(s), but {len(problems)} problem(s): {problems[0]}"
            )
        return f"{rows} engagement row(s), no problems"

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
        missing: list[str] = []
        for customer in names:
            try:
                accounting.customer_ref(customer)
            except Exception:
                missing.append(customer)
        if missing:
            raise RuntimeError(
                "QuickBooks has no customer called "
                + ", ".join(repr(entry) for entry in missing)
                + '. Add them in QuickBooks, or fix the "QuickBooks customer"'
                " column in the engagement list."
            )
        return f"all {len(names)} client name(s) exist in QuickBooks"

    return _run(name, run)
