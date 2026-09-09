"""The port for sending email, always draft-then-send so a restart can
reconcile an email that was in flight (docs/technical-design.md, Never twice).
"""

from enum import StrEnum
from typing import Protocol

from finance_ops_agent.domain.emails import OutgoingEmail


class DraftState(StrEnum):
    SENT = "sent"
    STILL_DRAFT = "still_draft"
    MISSING = "missing"


class EmailSender(Protocol):
    def create_draft(self, email: OutgoingEmail, attachments: dict[str, bytes]) -> str:
        """Create a draft with the attachment content (by filename); returns the
        draft id, stored before anything is sent."""
        ...

    def send(self, draft_id: str) -> None: ...

    def find_draft(self, draft_id: str) -> DraftState:
        """After a restart: was this draft sent, is it still a draft, or gone?"""
        ...
