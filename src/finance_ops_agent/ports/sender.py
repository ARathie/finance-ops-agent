"""The port for sending email.

The application makes the Message-ID and writes it down before calling send,
so a restart can look for the email in the Sent folder rather than resend
blindly (docs/integrations/email-imap-smtp.md, "Never twice, with SMTP").
"""

from typing import Protocol

from finance_ops_agent.domain.emails import OutgoingEmail


class NotSent(Exception):
    """The mail server did not take the email, and nothing left: the send can be
    retried. Raised only when the adapter is sure (a refused login, a refused
    connection, a rejected message). Anything ambiguous propagates as-is."""


class RecipientRefused(NotSent):
    """The server rejected an address; retrying will not help."""


class EmailSender(Protocol):
    def send(self, email: OutgoingEmail, attachments: dict[str, bytes], message_id: str) -> None:
        """Hand the email (with attachment content by filename) to the mail
        server under this Message-ID. Returns once the server has accepted it."""
        ...

    def save_sent_copy(
        self, email: OutgoingEmail, attachments: dict[str, bytes], message_id: str
    ) -> None:
        """Put a copy in the Sent folder so the mailbox is complete. A courtesy:
        the email was already sent when this is called."""
        ...

    def find_sent(self, message_id: str) -> bool:
        """After a restart: is an email with this Message-ID in the Sent folder?"""
        ...
