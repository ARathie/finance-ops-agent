"""Connections to the mailbox and the folders inside it.

`open_imap` and `open_smtp` speak the two ordinary email protocols with TLS
verification on; plain text (`none`) exists only for a test server on this
machine and config.py refuses it anywhere else. `Folders` discovers how this
particular server names folders (the hierarchy delimiter, and whether
everything lives under INBOX) so the agent's own folders land in the right place.
"""

import smtplib
import socket
import ssl
from dataclasses import dataclass

from imapclient import IMAPClient
from imapclient.exceptions import LoginError

SECURITY_CHOICES = ("ssl", "starttls", "none")


class MailboxProblem(Exception):
    """The mailbox could not be reached, or refused the login (MAILBOX_PROBLEM)."""


@dataclass(frozen=True)
class MailAccount:
    imap_host: str
    imap_port: int
    imap_security: str
    smtp_host: str
    smtp_port: int
    smtp_security: str
    username: str
    password: str
    folder_prefix: str = "Agent"
    sent_folder: str | None = None
    timeout: float = 60.0


def tls_context() -> ssl.SSLContext:
    """The default context verifies the certificate and the hostname."""
    context = ssl.create_default_context()
    assert context.check_hostname and context.verify_mode is ssl.CERT_REQUIRED
    return context


def open_imap(account: MailAccount) -> IMAPClient:
    try:
        if account.imap_security == "ssl":
            client = IMAPClient(
                account.imap_host,
                account.imap_port,
                ssl=True,
                ssl_context=tls_context(),
                timeout=account.timeout,
            )
        else:
            client = IMAPClient(
                account.imap_host, account.imap_port, ssl=False, timeout=account.timeout
            )
            if account.imap_security == "starttls":
                client.starttls(tls_context())
        client.login(account.username, account.password)
    except LoginError as error:
        raise MailboxProblem(
            f"the mailbox refused the login for {account.username}: {error}"
        ) from error
    except OSError as error:
        raise MailboxProblem(
            f"could not reach the mailbox at {account.imap_host}:{account.imap_port}: {error}"
        ) from error
    return client


def local_hostname() -> str:
    """The name this machine gives in EHLO. smtplib would look up the fully
    qualified name, which on some machines (macOS in particular) takes
    seconds per connection; the plain hostname is enough for a login."""
    return socket.gethostname() or "localhost"


def open_smtp(account: MailAccount) -> smtplib.SMTP:
    try:
        server: smtplib.SMTP
        if account.smtp_security == "ssl":
            server = smtplib.SMTP_SSL(
                account.smtp_host,
                account.smtp_port,
                local_hostname=local_hostname(),
                timeout=account.timeout,
                context=tls_context(),
            )
        else:
            server = smtplib.SMTP(
                account.smtp_host,
                account.smtp_port,
                local_hostname=local_hostname(),
                timeout=account.timeout,
            )
            server.ehlo()
            if account.smtp_security == "starttls":
                server.starttls(context=tls_context())
                server.ehlo()
        if server.has_extn("auth"):
            server.login(account.username, account.password)
    except smtplib.SMTPAuthenticationError as error:
        raise MailboxProblem(
            f"the mail server refused the login for {account.username}: {error}"
        ) from error
    except (OSError, smtplib.SMTPException) as error:
        raise MailboxProblem(
            f"could not reach the mail server at {account.smtp_host}:{account.smtp_port}: {error}"
        ) from error
    return server


class Folders:
    """How this server names folders, learned from LIST once per connection."""

    def __init__(self, client: IMAPClient, prefix: str) -> None:
        self._client = client
        self._prefix = prefix
        self.delimiter = "/"
        self.inbox_prefix = ""
        names: list[str] = []
        for _flags, delimiter, name in client.list_folders():
            if delimiter:
                self.delimiter = (
                    delimiter.decode("ascii") if isinstance(delimiter, bytes) else str(delimiter)
                )
            names.append(str(name))
        # Some servers keep every folder under INBOX ("INBOX.Sent"); follow suit.
        others = [name for name in names if name.upper() != "INBOX"]
        under_inbox = "INBOX" + self.delimiter
        if others and all(name.upper().startswith(under_inbox.upper()) for name in others):
            self.inbox_prefix = under_inbox
        self._flags = {
            str(name): tuple(flag for flag in flags) for flags, _, name in client.list_folders()
        }

    def agent_folder(self, name: str) -> str:
        """`Agent/<name>` (with this server's delimiter), created if missing."""
        parent = f"{self.inbox_prefix}{self._prefix}"
        path = f"{parent}{self.delimiter}{name}"
        for folder in (parent, path):
            if not self._client.folder_exists(folder):
                self._client.create_folder(folder)
        return path

    def sent_folder(self, configured: str | None) -> str:
        """The folder the server marks \\Sent; else the configured name; else Sent."""
        for name, flags in self._flags.items():
            if b"\\Sent" in flags:
                return name
        candidate = configured or f"{self.inbox_prefix}Sent"
        if not self._client.folder_exists(candidate):
            self._client.create_folder(candidate)
        return candidate
