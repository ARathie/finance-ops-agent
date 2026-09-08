"""A fake mailbox built from a folder of .eml files, sorted by filename.

The filename (without .eml) is the provider id, so fixtures are stable to name
and easy to reference in tests. The cursor is how many messages have been
handed over. `move` just records the folder, which tests can inspect.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from pathlib import Path

from finance_ops_agent.domain.messages import InboundAttachment, InboundEmail


@dataclass(frozen=True)
class _StoredEmail:
    email: InboundEmail
    attachment_content: dict[str, bytes]


def _parse_eml(provider_id: str, content: bytes) -> _StoredEmail:
    parsed = BytesParser(policy=policy.default).parsebytes(content)
    assert isinstance(parsed, EmailMessage)
    attachments: list[InboundAttachment] = []
    attachment_content: dict[str, bytes] = {}
    for index, part in enumerate(parsed.iter_attachments()):
        attachment_id = f"{provider_id}:{index}"
        decoded = part.get_payload(decode=True)
        payload = decoded if isinstance(decoded, bytes) else b""
        attachments.append(
            InboundAttachment(
                attachment_id=attachment_id,
                filename=part.get_filename() or f"attachment-{index}",
                mime_type=part.get_content_type(),
                size_bytes=len(payload),
            )
        )
        attachment_content[attachment_id] = payload
    date_header = parsed.get("Date")
    received_at = (
        parsedate_to_datetime(date_header) if date_header else datetime(2026, 1, 1, tzinfo=UTC)
    )
    body = parsed.get_body(preferencelist=("plain",))
    body_text = str(body.get_content()) if body is not None else ""
    email = InboundEmail(
        provider_id=provider_id,
        internet_message_id=(parsed.get("Message-ID") or "").strip(),
        conversation_id=(parsed.get("References") or parsed.get("Message-ID") or "").strip(),
        from_address=str(parsed.get("From", "")).split("<")[-1].rstrip(">").strip(),
        to_addresses=str(parsed.get("To", "")),
        subject=str(parsed.get("Subject", "")),
        body_text=body_text,
        received_at=received_at,
        attachments=tuple(attachments),
    )
    return _StoredEmail(email, attachment_content)


class FakeMailbox:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.folders: dict[str, str] = {}

    def _emails(self) -> list[_StoredEmail]:
        return [
            _parse_eml(path.stem, path.read_bytes())
            for path in sorted(self.directory.glob("*.eml"))
        ]

    def new_messages(self, cursor: str | None) -> tuple[list[InboundEmail], str]:
        emails = self._emails()
        start = int(cursor) if cursor else 0
        return [stored.email for stored in emails[start:]], str(len(emails))

    def download_attachment(self, provider_id: str, attachment_id: str) -> bytes:
        for stored in self._emails():
            if stored.email.provider_id == provider_id:
                return stored.attachment_content[attachment_id]
        raise KeyError(attachment_id)

    def move(self, provider_id: str, folder: str) -> None:
        self.folders[provider_id] = folder
