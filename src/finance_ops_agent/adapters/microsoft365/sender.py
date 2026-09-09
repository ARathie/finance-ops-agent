"""Sending mail through Graph (the EmailSender port), always draft-then-send.

Three steps, in this order and never collapsed: create the draft (its id is
saved by the caller before anything else happens), add the attachments, send.
That is what makes a crash between "about to send" and "sent" recoverable:
find_draft asks Graph what really happened to that draft.
"""

import base64

from finance_ops_agent.adapters.microsoft365.client import GraphClient, MailboxProblem
from finance_ops_agent.domain.emails import OutgoingEmail
from finance_ops_agent.ports.sender import DraftState

# Graph accepts contentBytes inline below ~3 MB; larger files need an upload
# session, which is chunked.
INLINE_ATTACHMENT_LIMIT = 3 * 1024 * 1024
UPLOAD_CHUNK = 4 * 320 * 1024  # Graph requires a multiple of 320 KiB


def _recipients(addresses: tuple[str, ...]) -> list[dict[str, dict[str, str]]]:
    return [{"emailAddress": {"address": address}} for address in addresses]


class GraphSender:
    def __init__(self, client: GraphClient) -> None:
        self._client = client

    def create_draft(self, email: OutgoingEmail, attachments: dict[str, bytes]) -> str:
        body: dict[str, object] = {
            "subject": email.subject,
            "body": {"contentType": "text", "content": email.body},
            "toRecipients": _recipients(email.to),
            "ccRecipients": _recipients(email.cc),
        }
        if email.reply_to:
            body["replyTo"] = _recipients((email.reply_to,))
        draft = self._client.post(self._client.user_url("/messages"), json=body)
        draft_id = str(draft["id"])
        for attachment in email.attachments:
            self._attach(draft_id, attachment.filename, attachments.get(attachment.filename, b""))
        return draft_id

    def _attach(self, draft_id: str, filename: str, content: bytes) -> None:
        if len(content) < INLINE_ATTACHMENT_LIMIT:
            self._client.post(
                self._client.user_url(f"/messages/{draft_id}/attachments"),
                json={
                    "@odata.type": "#microsoft.graph.fileAttachment",
                    "name": filename,
                    "contentBytes": base64.standard_b64encode(content).decode("ascii"),
                },
            )
            return
        self._attach_large(draft_id, filename, content)

    def _attach_large(self, draft_id: str, filename: str, content: bytes) -> None:
        session = self._client.post(
            self._client.user_url(f"/messages/{draft_id}/attachments/createUploadSession"),
            json={
                "AttachmentItem": {
                    "attachmentType": "file",
                    "name": filename,
                    "size": len(content),
                }
            },
        )
        upload_url = str(session["uploadUrl"])
        total = len(content)
        for start in range(0, total, UPLOAD_CHUNK):
            chunk = content[start : start + UPLOAD_CHUNK]
            end = start + len(chunk) - 1
            status = self._client.put_chunk(
                upload_url,
                chunk,
                {
                    "Content-Range": f"bytes {start}-{end}/{total}",
                    "Content-Length": str(len(chunk)),
                },
            )
            if status >= 400:
                raise MailboxProblem(
                    f"uploading {filename} failed at bytes {start}-{end}: {status}"
                )

    def send(self, draft_id: str) -> None:
        self._client.post(self._client.user_url(f"/messages/{draft_id}/send"))

    def find_draft(self, draft_id: str) -> DraftState:
        """After a restart: ask Graph what really happened to this draft."""
        try:
            raw = self._client.get(
                self._client.user_url(f"/messages/{draft_id}?$select=isDraft,sentDateTime")
            )
        except MailboxProblem as error:
            if "404" in str(error):
                return DraftState.MISSING
            raise
        if raw.get("isDraft"):
            return DraftState.STILL_DRAFT
        return DraftState.SENT
