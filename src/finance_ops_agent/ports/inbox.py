"""The port for reading the agent's mailbox."""

from typing import Protocol

from finance_ops_agent.domain.messages import InboundEmail

NEEDS_REVIEW_FOLDER = "Needs Review"


class EmailInbox(Protocol):
    def new_messages(self, cursor: str | None) -> tuple[list[InboundEmail], str]:
        """Messages that arrived since `cursor` (None = from the beginning),
        oldest first, plus the new cursor to save once they are stored."""
        ...

    def download_attachment(self, provider_id: str, attachment_id: str) -> bytes: ...

    def move(self, provider_id: str, folder: str) -> None: ...
