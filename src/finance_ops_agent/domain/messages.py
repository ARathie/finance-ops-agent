"""Emails as the agent sees them: fetched from the mailbox, then stored.

An InboundEmail is what the mailbox hands over; a StoredMessage is the agent's
own durable record of it (attachment content saved by sha256, the message
marked processed only once handling finished, so a restart picks up exactly
where the run stopped).

The key for a message is its Message-ID header (docs/integrations/email-imap-smtp.md).
Mailbox-side ids such as IMAP UIDs change when a message moves between folders,
so they are never stored. A message without a Message-ID (rare; broken senders)
gets a synthetic one made from its sender, date, subject, and body.
"""

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

SYNTHETIC_DOMAIN = "synthetic.finance-ops-agent"


def normalise_message_id(raw: str | None) -> str:
    """Trimmed, folding whitespace collapsed, angle brackets kept; "" when absent."""
    text = " ".join((raw or "").split())
    if not text:
        return ""
    if not text.startswith("<"):
        text = "<" + text
    if not text.endswith(">"):
        text = text + ">"
    return text


def synthetic_message_id(from_address: str, date_text: str, subject: str, body_text: str) -> str:
    """A stable stand-in for a missing Message-ID: the same email always maps
    to the same id, so a redelivery is still recognised."""
    digest = hashlib.sha256(
        "\n".join(
            [from_address.strip().casefold(), date_text.strip(), subject.strip(), body_text.strip()]
        ).encode("utf-8")
    ).hexdigest()
    return f"<synthetic-{digest[:32]}@{SYNTHETIC_DOMAIN}>"


def parse_references(raw: str | None) -> tuple[str, ...]:
    """Every <id> in a References (or In-Reply-To) header, normalised."""
    return tuple(normalise_message_id(part) for part in re.findall(r"<[^<>]+>", raw or ""))


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
    message_id: str  # the Message-ID header, normalised, or a synthetic one; the key
    in_reply_to: str  # "" when the email is not a reply
    references: tuple[str, ...]
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
    message_id: str
    in_reply_to: str
    references: tuple[str, ...]
    from_address: str
    to_addresses: str
    subject: str
    body_text: str
    received_at: datetime
    kind: MessageKind
    processed: bool
    attachments: tuple[StoredAttachment, ...]
