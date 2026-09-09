"""Reading the agent's mailbox through Graph (the EmailInbox port).

The delta link is the cursor. It is returned for the application to save only
after every message in the run has been stored; if it has expired (410) the
next run starts again from the beginning, which is safe because messages
already stored are skipped by id.
"""

from datetime import UTC, datetime
from typing import Any

from finance_ops_agent.adapters.microsoft365.client import DeltaExpired, GraphClient
from finance_ops_agent.domain.messages import InboundAttachment, InboundEmail

DELTA_SELECT = (
    "$select=id,internetMessageId,conversationId,from,toRecipients,subject,"
    "receivedDateTime,hasAttachments,isDraft"
)
FILE_ATTACHMENT = "#microsoft.graph.fileAttachment"
INLINE_ATTACHMENT_LIMIT = 1


def _address(entry: Any) -> str:
    if not isinstance(entry, dict):
        return ""
    address = entry.get("emailAddress")
    if isinstance(address, dict):
        return str(address.get("address") or "")
    return ""


def _received_at(text: str) -> datetime:
    if not text:
        return datetime(1970, 1, 1, tzinfo=UTC)
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


class GraphInbox:
    def __init__(self, client: GraphClient) -> None:
        self._client = client

    def _start_url(self, cursor: str | None) -> str:
        if cursor:
            return cursor
        return self._client.user_url(f"/mailFolders/inbox/messages/delta?{DELTA_SELECT}")

    def new_messages(self, cursor: str | None) -> tuple[list[InboundEmail], str]:
        try:
            return self._collect(self._start_url(cursor))
        except DeltaExpired:
            # The saved link aged out: read the folder from the beginning.
            # Messages already stored are skipped by id, so nothing repeats.
            return self._collect(self._start_url(None))

    def _collect(self, url: str) -> tuple[list[InboundEmail], str]:
        emails: list[InboundEmail] = []
        next_cursor = ""
        while url:
            page = self._client.get(url)
            for raw in page.get("value", []):
                if raw.get("isDraft") or "@removed" in raw:
                    continue  # the agent's own drafts, and deletions
                emails.append(self._to_email(raw))
            url = page.get("@odata.nextLink", "")
            if not url:
                next_cursor = str(page.get("@odata.deltaLink", "") or "")
        return emails, next_cursor

    def _to_email(self, raw: dict[str, Any]) -> InboundEmail:
        message_id = str(raw["id"])
        attachments: tuple[InboundAttachment, ...] = ()
        if raw.get("hasAttachments"):
            attachments = self._attachments(message_id)
        return InboundEmail(
            provider_id=message_id,
            internet_message_id=str(raw.get("internetMessageId") or ""),
            conversation_id=str(raw.get("conversationId") or ""),
            from_address=_address(raw.get("from")),
            to_addresses=", ".join(_address(entry) for entry in raw.get("toRecipients", [])),
            subject=str(raw.get("subject") or ""),
            body_text=self._body_text(message_id),
            received_at=_received_at(str(raw.get("receivedDateTime") or "")),
            attachments=attachments,
        )

    def _body_text(self, message_id: str) -> str:
        raw = self._client.get(
            self._client.user_url(f"/messages/{message_id}?$select=body"),
            headers={"Prefer": 'outlook.body-content-type="text"'},
        )
        body = raw.get("body")
        if isinstance(body, dict):
            return str(body.get("content") or "")
        return ""

    def _attachments(self, message_id: str) -> tuple[InboundAttachment, ...]:
        raw = self._client.get(self._client.user_url(f"/messages/{message_id}/attachments"))
        found: list[InboundAttachment] = []
        inline: list[InboundAttachment] = []
        for entry in raw.get("value", []):
            if entry.get("@odata.type") != FILE_ATTACHMENT:
                continue  # item attachments (a forwarded message) are not files
            attachment = InboundAttachment(
                attachment_id=str(entry["id"]),
                filename=str(entry.get("name") or "attachment"),
                mime_type=str(entry.get("contentType") or "application/octet-stream"),
                size_bytes=int(entry.get("size") or 0),
            )
            (inline if entry.get("isInline") else found).append(attachment)
        # Signature images are inline; only fall back to them when there is
        # nothing else, in case the timesheet itself was pasted in.
        return tuple(found or inline[:INLINE_ATTACHMENT_LIMIT])

    def download_attachment(self, provider_id: str, attachment_id: str) -> bytes:
        return self._client.get_bytes(
            self._client.user_url(f"/messages/{provider_id}/attachments/{attachment_id}/$value")
        )

    def move(self, provider_id: str, folder: str) -> None:
        destination = self._folder_id(folder)
        self._client.post(
            self._client.user_url(f"/messages/{provider_id}/move"),
            json={"destinationId": destination},
        )

    def _folder_id(self, folder: str) -> str:
        """The id of `Agent/<folder>`, creating the folders if they are missing."""
        agent = self._child_folder("msgfolderroot", "Agent")
        return self._child_folder(agent, folder)

    def _child_folder(self, parent_id: str, name: str) -> str:
        existing = self._client.get(
            self._client.user_url(f"/mailFolders/{parent_id}/childFolders?$select=id,displayName")
        )
        for entry in existing.get("value", []):
            if str(entry.get("displayName") or "").casefold() == name.casefold():
                return str(entry["id"])
        created = self._client.post(
            self._client.user_url(f"/mailFolders/{parent_id}/childFolders"),
            json={"displayName": name},
        )
        return str(created["id"])
