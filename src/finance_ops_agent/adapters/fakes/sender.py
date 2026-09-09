"""A fake email sender: drafts in memory, sent mail as .eml files in an outbox
folder. Crash hooks simulate dying between "about to send" and "sent"."""

from email.message import EmailMessage
from pathlib import Path

from finance_ops_agent.domain.emails import OutgoingEmail
from finance_ops_agent.ports.sender import DraftState

AGENT_ADDRESS = "jay@icon-technologies.com"


class SimulatedCrash(RuntimeError):
    pass


class FakeSender:
    def __init__(
        self,
        outbox: Path,
        crash_before_send: bool = False,
        crash_after_send: bool = False,
    ) -> None:
        self.outbox = outbox
        self.crash_before_send = crash_before_send
        self.crash_after_send = crash_after_send
        self.drafts: dict[str, tuple[OutgoingEmail, dict[str, bytes]]] = {}
        self.sent: list[str] = []

    def create_draft(self, email: OutgoingEmail, attachments: dict[str, bytes]) -> str:
        # Derived from the drafts already held, so a sender restarted on the
        # provider's existing state never reuses an id (as a real one would not).
        draft_id = f"draft-{len(self.drafts) + 1}"
        self.drafts[draft_id] = (email, attachments)
        return draft_id

    def send(self, draft_id: str) -> None:
        if draft_id not in self.drafts:
            raise KeyError(draft_id)
        if self.crash_before_send:
            raise SimulatedCrash("died between 'about to send' and 'sent'")
        email, attachments = self.drafts[draft_id]
        message = EmailMessage()
        message["From"] = AGENT_ADDRESS
        message["To"] = ", ".join(email.to)
        if email.cc:
            message["Cc"] = ", ".join(email.cc)
        if email.reply_to:
            message["Reply-To"] = email.reply_to
        message["Subject"] = email.subject
        message.set_content(email.body)
        for attachment in email.attachments:
            message.add_attachment(
                attachments.get(attachment.filename, b""),
                maintype="application",
                subtype="octet-stream",
                filename=attachment.filename,
            )
        self.outbox.mkdir(parents=True, exist_ok=True)
        (self.outbox / f"{len(self.sent) + 1:03d}-{draft_id}.eml").write_bytes(bytes(message))
        self.sent.append(draft_id)
        if self.crash_after_send:
            raise SimulatedCrash("died after the provider sent, before writing it down")

    def find_draft(self, draft_id: str) -> DraftState:
        if draft_id in self.sent:
            return DraftState.SENT
        if draft_id in self.drafts:
            return DraftState.STILL_DRAFT
        return DraftState.MISSING

    def sent_emails(self) -> list[OutgoingEmail]:
        return [self.drafts[draft_id][0] for draft_id in self.sent]
