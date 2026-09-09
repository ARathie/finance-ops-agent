"""A real mail server for the IMAP/SMTP contract tests: GreenMail, started
from its standalone jar on free local ports, with the agent (jay) and Kevin
as users. Plain text on localhost, which config.py allows only there.

The jar comes from Maven Central (see `GREENMAIL_URL`); CI downloads it and
sets `GREENMAIL_JAR`. Locally, run `uv run python tests/contract/get_greenmail.py`
once, or the tests here skip. When `CI` is set they fail instead, so the
adapter is never quietly untested.
"""

import os
import shutil
import socket
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from imapclient import IMAPClient

from finance_ops_agent.adapters.email.client import MailAccount, open_imap

GREENMAIL_VERSION = "2.1.3"
GREENMAIL_URL = (
    "https://repo1.maven.org/maven2/com/icegreen/greenmail-standalone/"
    f"{GREENMAIL_VERSION}/greenmail-standalone-{GREENMAIL_VERSION}.jar"
)
CACHED_JAR = (
    Path(__file__).parent.parent / ".cache" / f"greenmail-standalone-{GREENMAIL_VERSION}.jar"
)

AGENT = "jay@icon-technologies.com"
KEVIN = "kevin@icon-technologies.com"
PASSWORD = "not-a-real-password"


@dataclass(frozen=True)
class MailServer:
    host: str
    imap_port: int
    smtp_port: int

    def account(self, username: str = AGENT, **overrides: object) -> MailAccount:
        settings: dict[str, object] = {
            "imap_host": self.host,
            "imap_port": self.imap_port,
            "imap_security": "none",
            "smtp_host": self.host,
            "smtp_port": self.smtp_port,
            "smtp_security": "none",
            "username": username,
            "password": PASSWORD,
            "timeout": 10.0,
        }
        settings.update(overrides)
        return MailAccount(**settings)  # type: ignore[arg-type]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _jar() -> Path | None:
    configured = os.environ.get("GREENMAIL_JAR", "").strip()
    if configured:
        return Path(configured)
    return CACHED_JAR if CACHED_JAR.exists() else None


def _unavailable(reason: str) -> None:
    if os.environ.get("CI"):
        pytest.fail(f"the mail server tests must run in CI: {reason}")
    pytest.skip(reason)


@pytest.fixture(scope="session")
def mail_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[MailServer]:
    jar = _jar()
    if jar is None or not jar.exists():
        _unavailable(
            f"GreenMail jar not found; run tests/contract/get_greenmail.py ({GREENMAIL_URL})"
        )
        raise AssertionError  # unreachable; keeps mypy honest
    if shutil.which("java") is None:
        _unavailable("java is not installed (GreenMail needs it)")
    server = MailServer("127.0.0.1", _free_port(), _free_port())
    log = (tmp_path_factory.mktemp("greenmail") / "greenmail.log").open("w")
    process = subprocess.Popen(
        [
            "java",
            f"-Dgreenmail.smtp.hostname={server.host}",
            f"-Dgreenmail.smtp.port={server.smtp_port}",
            f"-Dgreenmail.imap.hostname={server.host}",
            f"-Dgreenmail.imap.port={server.imap_port}",
            f"-Dgreenmail.users=jay:{PASSWORD}@icon-technologies.com,kevin:{PASSWORD}@icon-technologies.com",
            "-Dgreenmail.users.login=email",
            "-Dgreenmail.verbose=false",
            "-jar",
            str(jar),
        ],
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    try:
        _wait_until_ready(server, process)
        yield server
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        log.close()


def _wait_until_ready(server: MailServer, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"GreenMail stopped with code {process.returncode}")
        try:
            client = open_imap(server.account())
        except Exception:
            time.sleep(0.25)
            continue
        client.logout()
        try:
            with socket.create_connection((server.host, server.smtp_port), timeout=2):
                return
        except OSError:
            time.sleep(0.25)
    raise RuntimeError("GreenMail did not start within 60 seconds")


def _empty_mailbox(client: IMAPClient) -> None:
    # Folders stay (GreenMail 2.1 drops the connection on DELETE); every
    # message in every folder goes.
    for _flags, _delim, name in client.list_folders():
        client.select_folder(str(name), readonly=False)
        uids = client.search(["ALL"])
        if uids:
            client.delete_messages(uids)
            client.expunge()
        client.close_folder()


@pytest.fixture
def mailbox(mail_server: MailServer) -> Iterator[MailServer]:
    """The server with both mailboxes emptied before the test."""
    for user in (AGENT, KEVIN):
        client = open_imap(mail_server.account(user))
        try:
            _empty_mailbox(client)
        finally:
            client.logout()
    yield mail_server
