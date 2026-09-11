"""Sending mail over SMTP, and keeping the Sent folder complete over IMAP
(the EmailSender port).

The Message-ID comes from the application, which wrote it down before calling
`send`. `send` returns only when the server has answered 250 to the message;
failures it is sure about (nothing left the server) are raised as NotSent so
the application retries; anything ambiguous propagates so the application
reconciles rather than resends.
"""

import mimetypes
import smtplib
from collections.abc import Callable
from datetime import datetime
from email.message import EmailMessage
from email.utils import format_datetime

from imapclient import IMAPClient

from finance_ops_agent.adapters.email.client import (
    Folders,
    MailAccount,
    MailboxProblem,
    open_imap,
    open_smtp,
)
from finance_ops_agent.domain.emails import OutgoingEmail
from finance_ops_agent.ports.sender import NotSent, RecipientRefused

MAX_MESSAGE_BYTES = 20 * 1024 * 1024


def describe_refusals(recipients: dict[str, tuple[int, bytes]]) -> str:
    """Name each refused address together with the reason the server gave.

    smtplib hands back `{address: (code, reason)}`. The address alone does not
    say what to do next: "no such mailbox" means the address is wrong, while
    "relaying denied" or a sending limit means the address is right and the
    mailbox is not allowed to send there. Whoever reads `fops doctor` or the
    SEND_FAILED review needs the difference.
    """
    described = []
    for address in sorted(recipients):
        code, reason = recipients[address]
        text = reason.decode("utf-8", "replace").strip()
        described.append(f"{address} ({code} {text})" if text else f"{address} ({code})")
    return "; ".join(described)


class SmtpSender:
    def __init__(
        self,
        account: MailAccount,
        agent_address: str,
        now: Callable[[], datetime],
        connect_smtp: Callable[[MailAccount], smtplib.SMTP] = open_smtp,
        connect_imap: Callable[[MailAccount], IMAPClient] = open_imap,
    ) -> None:
        self._account = account
        self._agent = agent_address
        self._now = now
        self._connect_smtp = connect_smtp
        self._connect_imap = connect_imap
        self._composed: dict[str, bytes] = {}

    def compose(
        self, email: OutgoingEmail, attachments: dict[str, bytes], message_id: str
    ) -> EmailMessage:
        message = EmailMessage()
        message["From"] = self._agent
        message["To"] = ", ".join(email.to)
        if email.cc:
            message["Cc"] = ", ".join(email.cc)
        if email.reply_to:
            message["Reply-To"] = email.reply_to
        if email.in_reply_to:
            message["In-Reply-To"] = email.in_reply_to
            message["References"] = email.in_reply_to
        message["Subject"] = email.subject
        message["Message-ID"] = message_id
        message["Date"] = format_datetime(self._now())
        message.set_content(email.body)
        for attachment in email.attachments:
            content = attachments.get(attachment.filename, b"")
            guessed, _ = mimetypes.guess_type(attachment.filename)
            maintype, subtype = (guessed or "application/octet-stream").split("/", 1)
            message.add_attachment(
                content, maintype=maintype, subtype=subtype, filename=attachment.filename
            )
        raw = bytes(message)
        if len(raw) > MAX_MESSAGE_BYTES:
            raise NotSent(
                f"the email is {len(raw) // (1024 * 1024)} MB with attachments; the limit is 20 MB"
            )
        self._composed[message_id] = raw
        return message

    def send(self, email: OutgoingEmail, attachments: dict[str, bytes], message_id: str) -> None:
        message = self.compose(email, attachments, message_id)
        try:
            server = self._connect_smtp(self._account)
        except MailboxProblem as error:
            raise NotSent(str(error)) from error
        try:
            with server:
                server.send_message(message)
        except smtplib.SMTPRecipientsRefused as error:
            refused = describe_refusals(error.recipients)
            raise RecipientRefused(f"the mail server refused the address(es): {refused}") from error
        except (
            smtplib.SMTPSenderRefused,
            smtplib.SMTPDataError,
            smtplib.SMTPHeloError,
            smtplib.SMTPNotSupportedError,
            smtplib.SMTPAuthenticationError,
        ) as error:
            # The server answered with a refusal: nothing left, retry later.
            raise NotSent(str(error)) from error
        # A dropped connection or a timeout mid-transaction is ambiguous and
        # propagates as-is: the application reconciles it, never resends blindly.

    def _raw(self, email: OutgoingEmail, attachments: dict[str, bytes], message_id: str) -> bytes:
        raw = self._composed.get(message_id)
        if raw is None:
            raw = bytes(self.compose(email, attachments, message_id))
        return raw

    def save_sent_copy(
        self, email: OutgoingEmail, attachments: dict[str, bytes], message_id: str
    ) -> None:
        client = self._connect_imap(self._account)
        try:
            folder = Folders(client, self._account.folder_prefix).sent_folder(
                self._account.sent_folder
            )
            client.append(
                folder, self._raw(email, attachments, message_id), flags=[b"\\Seen"], msg_time=None
            )
        finally:
            client.logout()

    def find_sent(self, message_id: str) -> bool:
        client = self._connect_imap(self._account)
        try:
            folder = Folders(client, self._account.folder_prefix).sent_folder(
                self._account.sent_folder
            )
            client.select_folder(folder, readonly=True)
            return bool(client.search(["HEADER", "Message-ID", message_id]))
        finally:
            client.logout()
