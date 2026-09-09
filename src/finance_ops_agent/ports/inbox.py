"""The port for reading the agent's mailbox."""

from typing import Protocol

from finance_ops_agent.domain.messages import InboundEmail

NEEDS_REVIEW_FOLDER = "Needs Review"
PROCESSED_FOLDER = "Processed"
IGNORED_FOLDER = "Ignored"


class EmailInbox(Protocol):
    def new_messages(self, position: str | None) -> tuple[list[InboundEmail], str]:
        """Messages that arrived since `position` (None = from the beginning),
        oldest first, plus the new position to save once they are stored.

        The position is opaque to the application; the IMAP adapter keeps the
        folder's UIDVALIDITY and the last UID seen in it."""
        ...

    def download_attachment(self, message_id: str, attachment_id: str) -> bytes: ...

    def move(self, message_id: str, folder: str) -> None:
        """Move the message into the agent's folder of that name. A courtesy for
        a human looking at the mailbox; the database is the record."""
        ...
