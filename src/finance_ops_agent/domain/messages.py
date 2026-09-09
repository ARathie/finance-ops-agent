"""Emails as the agent sees them: fetched from the mailbox, then stored.

An InboundEmail is what the mailbox hands over; a StoredMessage is the agent's
own durable record of it (attachment content saved by sha256, the message
marked processed only once handling finished, so a restart picks up exactly
where the run stopped).
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class MessageKind(StrEnum):
    """What an email is, decided by code from the sender and the engagement list."""

    TIMESHEET = "timesheet"
    KEVIN_REPLY = "kevin_reply"
    CLIENT_REPLY = "client_reply"
    UNKNOWN_SENDER = "unknown_sender"
    OTHER = "other"


@dataclass(frozen=True)
class InboundAttachment:
    attachment_id: str
    filename: str
    mime_type: str
    size_bytes: int


@dataclass(frozen=True)
class InboundEmail:
    provider_id: str
    internet_message_id: str
    conversation_id: str
    from_address: str
    to_addresses: str
    subject: str
    body_text: str
    received_at: datetime
    attachments: tuple[InboundAttachment, ...]


@dataclass(frozen=True)
class StoredAttachment:
    filename: str
    mime_type: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class StoredMessage:
    provider_id: str
    internet_message_id: str
    conversation_id: str
    from_address: str
    to_addresses: str
    subject: str
    body_text: str
    received_at: datetime
    kind: MessageKind
    processed: bool
    attachments: tuple[StoredAttachment, ...]
