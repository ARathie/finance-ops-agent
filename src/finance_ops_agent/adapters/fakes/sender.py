"""A fake email sender: sent mail as .eml files in an outbox folder, and a
pretend Sent folder. Crash hooks simulate dying just before or just after the
mail server accepts an email."""

from email.message import EmailMessage
from pathlib import Path

from finance_ops_agent.domain.emails import OutgoingEmail

AGENT_ADDRESS = "jay@icon-technologies.com"


class SimulatedCrash(RuntimeError):
    pass


class FakeSender:
    def __init__(
        self,
        outbox: Path,
        crash_before_send: bool = False,
        crash_after_send: bool = False,
        provider_saves_sent: bool = False,
    ) -> None:
        self.outbox = outbox
        self.crash_before_send = crash_before_send
        self.crash_after_send = crash_after_send
        # Some providers file a copy in Sent by themselves; Rackspace does not.
        self.provider_saves_sent = provider_saves_sent
        self.sent: list[str] = []  # message ids, in the order the server took them
        self.emails: dict[str, OutgoingEmail] = {}
        self.sent_copies: set[str] = set()

    def send(self, email: OutgoingEmail, attachments: dict[str, bytes], message_id: str) -> None:
        if self.crash_before_send:
            raise SimulatedCrash("died before the mail server took it")
        message = EmailMessage()
        message["From"] = AGENT_ADDRESS
        message["To"] = ", ".join(email.to)
        if email.cc:
            message["Cc"] = ", ".join(email.cc)
        if email.reply_to:
            message["Reply-To"] = email.reply_to
        if email.in_reply_to:
            message["In-Reply-To"] = email.in_reply_to
            message["References"] = email.in_reply_to
        message["Subject"] = email.subject
        message["Message-ID"] = message_id
        message.set_content(email.body)
        for attachment in email.attachments:
            message.add_attachment(
                attachments.get(attachment.filename, b""),
                maintype="application",
                subtype="octet-stream",
                filename=attachment.filename,
            )
        self.outbox.mkdir(parents=True, exist_ok=True)
        (self.outbox / f"{len(self.sent) + 1:03d}.eml").write_bytes(bytes(message))
        self.sent.append(message_id)
        self.emails[message_id] = email
        if self.provider_saves_sent:
            self.sent_copies.add(message_id)
        if self.crash_after_send:
            raise SimulatedCrash("died after the mail server took it, before writing it down")

    def save_sent_copy(
        self, email: OutgoingEmail, attachments: dict[str, bytes], message_id: str
    ) -> None:
        self.sent_copies.add(message_id)

    def find_sent(self, message_id: str) -> bool:
        return message_id in self.sent_copies

    def sent_emails(self) -> list[OutgoingEmail]:
        return [self.emails[message_id] for message_id in self.sent]
