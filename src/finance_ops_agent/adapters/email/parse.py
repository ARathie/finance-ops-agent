"""A raw email (RFC 5322 bytes) to the domain's InboundEmail.

Used by the IMAP adapter and by the fake mailbox, so the two never disagree
about what an attachment is or where the Message-ID comes from.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import parseaddr, parsedate_to_datetime

from finance_ops_agent.domain.messages import (
    InboundAttachment,
    InboundEmail,
    normalise_message_id,
    parse_references,
    synthetic_message_id,
)

INLINE_ATTACHMENT_LIMIT = 1
_TAGS = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class ParsedMessage:
    email: InboundEmail
    attachment_content: dict[str, bytes]


def _body_text(parsed: EmailMessage) -> str:
    body = parsed.get_body(preferencelist=("plain", "html"))
    if body is None:
        return ""
    text = str(body.get_content())
    if body.get_content_type() == "text/html":
        text = _TAGS.sub(" ", text)
        text = " ".join(text.split())
    return text


def _received_at(parsed: EmailMessage, fallback: datetime | None) -> datetime:
    date_header = parsed.get("Date")
    if date_header:
        try:
            value = parsedate_to_datetime(date_header)
            return value if value.tzinfo else value.replace(tzinfo=UTC)
        except (TypeError, ValueError):
            pass
    if fallback is not None:
        return fallback if fallback.tzinfo else fallback.replace(tzinfo=UTC)
    return datetime(1970, 1, 1, tzinfo=UTC)


def _is_inline_image(part: EmailMessage) -> bool:
    return (
        part.get_content_maintype() == "image"
        and part.get("Content-ID") is not None
        and not part.is_attachment()
    )


def parse_message(raw: bytes, received_fallback: datetime | None = None) -> ParsedMessage:
    parsed = BytesParser(policy=policy.default).parsebytes(raw)
    assert isinstance(parsed, EmailMessage)
    from_address = parseaddr(str(parsed.get("From", "")))[1].strip()
    subject = str(parsed.get("Subject", ""))
    body_text = _body_text(parsed)
    message_id = normalise_message_id(parsed.get("Message-ID")) or synthetic_message_id(
        from_address, str(parsed.get("Date", "")), subject, body_text
    )

    regular: list[InboundAttachment] = []
    inline: list[InboundAttachment] = []
    content: dict[str, bytes] = {}
    for index, part in enumerate(parsed.iter_attachments()):
        assert isinstance(part, EmailMessage)
        attachment_id = f"{message_id}:{index}"
        decoded = part.get_payload(decode=True)
        payload = decoded if isinstance(decoded, bytes) else b""
        attachment = InboundAttachment(
            attachment_id=attachment_id,
            filename=part.get_filename() or f"attachment-{index}",
            mime_type=part.get_content_type(),
            size_bytes=len(payload),
        )
        content[attachment_id] = payload
        (inline if _is_inline_image(part) else regular).append(attachment)
    # Signature images are inline; only fall back to them when there is
    # nothing else, in case the timesheet itself was pasted in.
    attachments = tuple(regular or inline[:INLINE_ATTACHMENT_LIMIT])
    keep = {attachment.attachment_id for attachment in attachments}

    email = InboundEmail(
        message_id=message_id,
        in_reply_to=normalise_message_id(parsed.get("In-Reply-To")),
        references=parse_references(parsed.get("References")),
        from_address=from_address,
        to_addresses=str(parsed.get("To", "")),
        subject=subject,
        body_text=body_text,
        received_at=_received_at(parsed, received_fallback),
        attachments=attachments,
    )
    return ParsedMessage(email, {key: value for key, value in content.items() if key in keep})
