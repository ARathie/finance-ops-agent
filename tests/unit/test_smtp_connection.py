"""Connecting to the SMTP server: the agent authenticates before it sends.

These run with no server. A mail server does not reveal its extension list
until it has been greeted, so `has_extn("auth")` is False on a fresh
connection and a login guarded by it would be skipped. The local test server
only ever exercises the plain-text path, so the SSL path Rackspace uses -- the
one where `SMTP_SSL` greets nobody by itself -- needs a stub that behaves the
same way a real server does.
"""

import smtplib

import pytest

from finance_ops_agent.adapters.email.client import MailAccount, MailboxProblem, open_smtp

AGENT = "jay@icon-technologies.com"
PASSWORD = "long-random-password"


class StubServer:
    """Enough of `smtplib.SMTP` to watch the greeting and the login."""

    def __init__(self, offers_auth: bool) -> None:
        self._offers_auth = offers_auth
        self.esmtp_features: dict[str, str] = {}
        self.greetings = 0
        self.logins: list[tuple[str, str]] = []
        self.started_tls = False

    def ehlo(self) -> tuple[int, bytes]:
        # Exactly like a real server: the extensions are unknown until now.
        self.greetings += 1
        if self._offers_auth:
            self.esmtp_features["auth"] = "PLAIN LOGIN"
        return (250, b"ok")

    def has_extn(self, opt: str) -> bool:
        return opt.lower() in self.esmtp_features

    def login(self, user: str, password: str) -> tuple[int, bytes]:
        self.logins.append((user, password))
        return (235, b"ok")

    def starttls(self, context: object = None) -> None:
        self.started_tls = True


def install(monkeypatch: pytest.MonkeyPatch, offers_auth: bool = True) -> list[StubServer]:
    made: list[StubServer] = []

    def factory(*args: object, **kwargs: object) -> StubServer:
        server = StubServer(offers_auth)
        made.append(server)
        return server

    monkeypatch.setattr(smtplib, "SMTP_SSL", factory)
    monkeypatch.setattr(smtplib, "SMTP", factory)
    return made


def account(security: str) -> MailAccount:
    return MailAccount(
        imap_host="secure.emailsrvr.com",
        imap_port=993,
        imap_security="ssl",
        smtp_host="secure.emailsrvr.com",
        smtp_port=465,
        smtp_security=security,
        username=AGENT,
        password=PASSWORD,
    )


def test_the_ssl_path_greets_the_server_and_logs_in(monkeypatch: pytest.MonkeyPatch) -> None:
    made = install(monkeypatch)

    open_smtp(account("ssl"))

    [server] = made
    assert server.greetings == 1, "ungreeted, the server never admits it offers a login"
    assert server.logins == [(AGENT, PASSWORD)]


def test_the_starttls_path_logs_in_after_the_second_greeting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    made = install(monkeypatch)

    open_smtp(account("starttls"))

    [server] = made
    assert server.started_tls
    assert server.logins == [(AGENT, PASSWORD)]


def test_a_real_server_with_no_login_is_refused_rather_than_sent_to(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    made = install(monkeypatch, offers_auth=False)

    with pytest.raises(MailboxProblem, match="unauthenticated"):
        open_smtp(account("ssl"))

    assert made[0].logins == []


def test_a_plain_test_server_on_this_machine_may_offer_no_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    made = install(monkeypatch, offers_auth=False)

    open_smtp(account("none"))

    assert made[0].logins == []
