"""The IMAP/SMTP adapter against a real mail server (GreenMail, see conftest).

Every case in the reading and sending sections of
docs/integrations/email-imap-smtp.md is here, driven through the same
application code the fakes run under, so what the fakes promise the real
adapter is shown to keep. Nothing here ever talks to Rackspace.
"""

import smtplib
import ssl
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from email.message import EmailMessage
from email.utils import make_msgid
from pathlib import Path

import pytest
from imapclient import IMAPClient

from finance_ops_agent.adapters.email.client import (
    Folders,
    MailAccount,
    MailboxProblem,
    open_imap,
    tls_context,
)
from finance_ops_agent.adapters.email.inbox import ImapInbox, decode_position, encode_position
from finance_ops_agent.adapters.email.sender import SmtpSender
from finance_ops_agent.adapters.fakes.accounting import FakeAccounting
from finance_ops_agent.adapters.fakes.clock import FakeClock
from finance_ops_agent.adapters.fakes.engagement_list import FakeEngagementList
from finance_ops_agent.adapters.fakes.reader import FakeReader
from finance_ops_agent.adapters.fakes.store import FakeStore
from finance_ops_agent.adapters.pdf.writer import TextPdfRenderer
from finance_ops_agent.application.outgoing import send_pending
from finance_ops_agent.application.run import (
    MAILBOX_POSITION_KEY,
    Mode,
    RunDeps,
    RunReport,
    Settings,
    run_once,
)
from finance_ops_agent.cli.doctor import _describe_claude_model as _real_describe_claude_model
from finance_ops_agent.domain.emails import OutgoingEmail
from finance_ops_agent.domain.messages import SYNTHETIC_DOMAIN
from finance_ops_agent.domain.reading import (
    ReplyAnswer,
    ReplyAnswerKind,
    ReplyReading,
    TimesheetReading,
)
from finance_ops_agent.domain.statuses import ItemStatus
from finance_ops_agent.ports.inbox import NEEDS_REVIEW_FOLDER, PROCESSED_FOLDER
from finance_ops_agent.ports.sender import NotSent, RecipientRefused
from tests.contract.conftest import AGENT, KEVIN, MailServer
from tests.scenarios.conftest import PRIYA, TODAY, default_workbook, reading

AUG = (date(2026, 8, 1), date(2026, 8, 31))
HOUR = timedelta(hours=1)
NOW = datetime.combine(TODAY, datetime.min.time(), tzinfo=UTC).replace(hour=12)


# --- helpers ---------------------------------------------------------------


def timesheet_email(
    from_address: str = PRIYA,
    subject: str = "August timesheet",
    attachment: tuple[str, bytes] | None = ("timesheet.pdf", b"%PDF-1.4 fake"),
    message_id: str | None = "<aug@example>",
    body: str = "Approved timesheet attached.",
) -> EmailMessage:
    message = EmailMessage()
    message["From"] = from_address
    message["To"] = AGENT
    message["Subject"] = subject
    message["Date"] = "Tue, 08 Sep 2026 09:00:00 -0400"
    if message_id:
        message["Message-ID"] = message_id
    message.set_content(body)
    if attachment:
        name, content = attachment
        message.add_attachment(content, maintype="application", subtype="pdf", filename=name)
    return message


def deliver(server: MailServer, message: EmailMessage, to: str = AGENT) -> None:
    """Hand a message to the server over SMTP, as the sender's mail program would."""
    with smtplib.SMTP(
        server.host, server.smtp_port, local_hostname="localhost", timeout=10
    ) as smtp:
        smtp.sendmail(str(message["From"]), [to], bytes(message))


def messages_in(server: MailServer, user: str, folder: str = "INBOX") -> list[EmailMessage]:
    from email import policy
    from email.parser import BytesParser

    client = open_imap(server.account(user))
    try:
        if not client.folder_exists(folder):
            return []
        client.select_folder(folder, readonly=True)
        uids = client.search(["ALL"])
        found = client.fetch(uids, ["BODY.PEEK[]"]) if uids else {}
        parsed = [
            BytesParser(policy=policy.default).parsebytes(found[uid][b"BODY[]"]) for uid in uids
        ]
        return [message for message in parsed if isinstance(message, EmailMessage)]
    finally:
        client.logout()


def subjects_in(server: MailServer, user: str, folder: str = "INBOX") -> list[str]:
    return [str(message["Subject"]) for message in messages_in(server, user, folder)]


@dataclass
class Env:
    """The run on the real mailbox with everything else faked."""

    server: MailServer
    store: FakeStore = field(default_factory=FakeStore)
    readings: dict[str, TimesheetReading] = field(default_factory=dict)
    replies: dict[str, ReplyReading] = field(default_factory=dict)
    mode: Mode = Mode.DRY_RUN
    now: datetime = NOW
    start_date: date | None = None

    def inbox(self) -> ImapInbox:
        return ImapInbox(self.server.account(), AGENT, self.start_date)

    def sender(self) -> SmtpSender:
        return SmtpSender(self.server.account(), AGENT, lambda: self.now)

    def deps(self) -> RunDeps:
        return RunDeps(
            engagement_list=FakeEngagementList(default_workbook()),
            inbox=self.inbox(),
            reader=FakeReader(self.readings, self.replies),
            store=self.store,
            clock=FakeClock(self.now.date(), self.now),
            settings=Settings(mode=self.mode),
            sender=self.sender(),
            accounting=FakeAccounting(),
            renderer=TextPdfRenderer(),
        )

    def run(self) -> RunReport:
        return run_once(self.deps())


@pytest.fixture
def env(mailbox: MailServer) -> Env:
    return Env(mailbox)


# --- reading ---------------------------------------------------------------


class TestReading:
    def test_new_mail_by_uid_and_the_position_advances(self, env: Env) -> None:
        deliver(env.server, timesheet_email(message_id="<one@example>"))
        deliver(env.server, timesheet_email(message_id="<two@example>", subject="Second"))
        inbox = env.inbox()

        emails, position = inbox.new_messages(None)
        assert [email.message_id for email in emails] == ["<one@example>", "<two@example>"]
        assert emails[0].from_address == PRIYA
        assert emails[0].subject == "August timesheet"
        assert emails[0].body_text.strip() == "Approved timesheet attached."
        assert emails[0].received_at == datetime(2026, 9, 8, 13, 0, tzinfo=UTC)
        [attachment] = emails[0].attachments
        assert (attachment.filename, attachment.mime_type) == ("timesheet.pdf", "application/pdf")
        assert inbox.download_attachment("<one@example>", attachment.attachment_id) == (
            b"%PDF-1.4 fake"
        )

        # Nothing new: the position holds and nothing is handed over twice.
        again, same = inbox.new_messages(position)
        assert again == [] and same == position

        deliver(env.server, timesheet_email(message_id="<three@example>", subject="Third"))
        later, advanced = inbox.new_messages(position)
        assert [email.message_id for email in later] == ["<three@example>"]
        assert advanced != position

    def test_a_fresh_connection_can_still_download_an_attachment(self, env: Env) -> None:
        """A restart between reading and downloading finds the message by its Message-ID."""
        deliver(env.server, timesheet_email())
        emails, _ = env.inbox().new_messages(None)
        [attachment] = emails[0].attachments
        content = env.inbox().download_attachment("<aug@example>", attachment.attachment_id)
        assert content == b"%PDF-1.4 fake"
        with pytest.raises(MailboxProblem, match="no longer in the inbox"):
            env.inbox().download_attachment("<gone@example>", "<gone@example>:0")

    def test_a_rebuilt_inbox_is_read_again_without_duplicate_rows(self, env: Env) -> None:
        """A changed UIDVALIDITY means the UIDs mean nothing any more: the agent
        reads the whole inbox again, and the Message-ID keeps every row unique."""
        env.readings["timesheet.pdf"] = reading(*AUG)
        deliver(env.server, timesheet_email())
        first = env.run()
        assert first.messages_stored == 1
        assert len(env.store.list_items()) == 1

        position = env.store.get_state(MAILBOX_POSITION_KEY)
        assert position is not None
        import json

        stale = json.loads(position)
        assert decode_position(position, stale["uidvalidity"]) == stale["last_uid"]
        assert decode_position(position, stale["uidvalidity"] + 1) == 0
        assert decode_position("not json", stale["uidvalidity"]) == 0
        env.store.set_state(
            MAILBOX_POSITION_KEY, encode_position(stale["uidvalidity"] + 1, stale["last_uid"])
        )

        # The message was moved to Processed by the first run; put a copy back
        # in the inbox to stand in for a server that rebuilt the folder.
        deliver(env.server, timesheet_email())
        second = env.run()
        assert second.messages_stored == 0, "the same Message-ID is not a new row"
        assert len(env.store.list_items()) == 1

    def test_a_redelivered_message_is_skipped_by_message_id(self, env: Env) -> None:
        env.readings["timesheet.pdf"] = reading(*AUG)
        deliver(env.server, timesheet_email())
        deliver(env.server, timesheet_email())  # the same email, delivered twice

        report = env.run()
        assert report.messages_stored == 1
        assert len(env.store.list_items()) == 1
        assert len(env.store.outgoing_records()) == len(
            {r.idempotency_key for r in env.store.outgoing_records()}
        )

    def test_a_message_without_a_message_id_gets_a_stable_synthetic_one(self, env: Env) -> None:
        deliver(env.server, timesheet_email(message_id=None))
        deliver(env.server, timesheet_email(message_id=None))
        emails, _ = env.inbox().new_messages(None)
        assert len(emails) == 2
        ids = {email.message_id for email in emails}
        assert len(ids) == 1, "the same email always gets the same synthetic id"
        [synthetic] = ids
        assert synthetic.startswith("<synthetic-") and synthetic.endswith(f"@{SYNTHETIC_DOMAIN}>")

    def test_inline_images_are_ignored_unless_alone(self, env: Env) -> None:
        with_pdf = timesheet_email(message_id="<pdf@example>")
        with_pdf.add_attachment(
            b"PNG",
            maintype="image",
            subtype="png",
            filename="logo.png",
            disposition="inline",
            cid=make_msgid(domain="example"),
        )
        deliver(env.server, with_pdf)
        only_image = timesheet_email(message_id="<img@example>", attachment=None)
        only_image.add_attachment(
            b"PNG",
            maintype="image",
            subtype="png",
            filename="pasted.png",
            disposition="inline",
            cid=make_msgid(domain="example"),
        )
        deliver(env.server, only_image)

        emails, _ = env.inbox().new_messages(None)
        by_id = {email.message_id: email for email in emails}
        assert [a.filename for a in by_id["<pdf@example>"].attachments] == ["timesheet.pdf"]
        assert [a.filename for a in by_id["<img@example>"].attachments] == ["pasted.png"]

    def test_the_agents_own_mail_is_not_read_as_new(self, env: Env) -> None:
        deliver(env.server, timesheet_email(from_address=AGENT, message_id="<mine@example>"))
        deliver(env.server, timesheet_email(message_id="<hers@example>"))
        emails, _ = env.inbox().new_messages(None)
        assert [email.message_id for email in emails] == ["<hers@example>"]

    def test_mail_before_the_start_date_is_left_alone(self, env: Env) -> None:
        """MAIL_START_DATE: whatever was in the mailbox before the agent
        started is Kevin's business, not the agent's."""
        client = open_imap(env.server.account())
        try:
            client.append(
                "INBOX",
                bytes(timesheet_email(message_id="<old@example>")),
                msg_time=datetime(2026, 8, 20, 9, 0, tzinfo=UTC),
            )
            client.append(
                "INBOX",
                bytes(timesheet_email(message_id="<new@example>")),
                msg_time=datetime(2026, 9, 9, 9, 0, tzinfo=UTC),
            )
        finally:
            client.logout()
        env.start_date = date(2026, 9, 9)
        emails, _ = env.inbox().new_messages(None)
        assert [email.message_id for email in emails] == ["<new@example>"]
        env.start_date = None
        everything, _ = env.inbox().new_messages(None)
        assert len(everything) == 2

    def test_a_processed_timesheet_is_filed_under_agent(self, env: Env) -> None:
        env.readings["timesheet.pdf"] = reading(*AUG)
        deliver(env.server, timesheet_email())
        env.run()
        # GreenMail's delimiter is "."; the folder name follows the server.
        assert subjects_in(env.server, AGENT, f"Agent.{PROCESSED_FOLDER}") == ["August timesheet"]
        assert subjects_in(env.server, AGENT) == []

    def test_an_unknown_sender_is_filed_under_needs_review(self, env: Env) -> None:
        deliver(env.server, timesheet_email(from_address="stranger@nowhere.example"))
        report = env.run()
        assert report.unknown_senders == 1
        assert subjects_in(env.server, AGENT, f"Agent.{NEEDS_REVIEW_FOLDER}") == [
            "August timesheet"
        ]
        assert subjects_in(env.server, AGENT) == []

    def test_move_falls_back_to_copy_and_delete(self, env: Env) -> None:
        """A server without MOVE gets the same result the slow way."""

        def without_move(account: MailAccount) -> IMAPClient:
            client = open_imap(account)
            original = client.has_capability
            client.has_capability = lambda name: False if name == "MOVE" else original(name)
            return client

        deliver(env.server, timesheet_email())
        inbox = ImapInbox(env.server.account(), AGENT, None, connect=without_move)
        inbox.new_messages(None)
        inbox.move("<aug@example>", PROCESSED_FOLDER)
        assert subjects_in(env.server, AGENT, f"Agent.{PROCESSED_FOLDER}") == ["August timesheet"]
        assert subjects_in(env.server, AGENT) == []
        inbox.move("<never-there@example>", PROCESSED_FOLDER)  # quietly nothing


class TestFolders:
    """Folder names follow the server: its delimiter, and whether everything
    lives under INBOX. Real servers differ (Rackspace uses "/" at the top level,
    GreenMail "." — and some put every folder under INBOX)."""

    def test_on_the_real_server(self, mailbox: MailServer) -> None:
        client = open_imap(mailbox.account())
        try:
            folders = Folders(client, "Agent")
            assert folders.delimiter == "."
            assert folders.agent_folder("Processed") == "Agent.Processed"
            assert client.folder_exists("Agent") and client.folder_exists("Agent.Processed")
            assert folders.agent_folder("Processed") == "Agent.Processed"  # idempotent
            # GreenMail marks no folder \Sent, so the configured name or "Sent" is used.
            assert folders.sent_folder(None) == "Sent"
            assert folders.sent_folder("Sent Items") == "Sent Items"
            assert client.folder_exists("Sent Items")
            assert folders.sent_folder(None) == "Sent"  # the configured name is not remembered
        finally:
            client.logout()

    def test_everything_under_inbox_with_a_slash(self) -> None:
        stub = _StubImap(
            delimiter="/",
            folders=["INBOX", "INBOX/Drafts", "INBOX/Sent"],
            flags={"INBOX/Sent": (b"\\Sent",)},
        )
        folders = Folders(stub, "Agent")
        assert folders.inbox_prefix == "INBOX/"
        assert folders.agent_folder("Needs Review") == "INBOX/Agent/Needs Review"
        assert stub.created == ["INBOX/Agent", "INBOX/Agent/Needs Review"]
        assert folders.sent_folder(None) == "INBOX/Sent"  # the \Sent flag wins

    def test_top_level_folders_with_a_slash(self) -> None:
        stub = _StubImap(delimiter="/", folders=["INBOX", "Sent", "Trash"], flags={})
        folders = Folders(stub, "Agent")
        assert folders.inbox_prefix == ""
        assert folders.agent_folder("Ignored") == "Agent/Ignored"
        assert folders.sent_folder("Sent") == "Sent"
        assert "Sent" not in stub.created


class _StubImap:
    def __init__(
        self, delimiter: str, folders: list[str], flags: dict[str, tuple[bytes, ...]]
    ) -> None:
        self.delimiter = delimiter.encode()
        self.folders = list(folders)
        self.flags = flags
        self.created: list[str] = []

    def list_folders(self) -> list[tuple[tuple[bytes, ...], bytes, str]]:
        return [(self.flags.get(name, ()), self.delimiter, name) for name in self.folders]

    def folder_exists(self, name: str) -> bool:
        return name in self.folders

    def create_folder(self, name: str) -> None:
        self.folders.append(name)
        self.created.append(name)


# --- sending ---------------------------------------------------------------


def _record_in_flight(
    store: FakeStore,
    key: str,
    email: OutgoingEmail,
    message_id: str,
    started_at: datetime,
    accepted: bool = False,
) -> None:
    assert store.record_outgoing("billing_email", key, None, email.payload())
    store.update_outgoing(
        key,
        status="in_flight",
        message_id=message_id,
        started_at=started_at.isoformat(),
        accepted_at=started_at.isoformat() if accepted else None,
    )


KEVIN_EMAIL = OutgoingEmail(
    to=(KEVIN,), subject="Icon Technologies invoice 1 — test", body="Hello Kevin"
)


class TestSending:
    def test_send_then_a_copy_in_sent(self, env: Env) -> None:
        sender = env.sender()
        message_id = make_msgid(domain="icon-technologies.com")
        sender.send(KEVIN_EMAIL, {}, message_id)

        [received] = messages_in(env.server, KEVIN)
        assert received["Message-ID"] == message_id
        assert received["From"] == AGENT
        assert received["Subject"] == KEVIN_EMAIL.subject
        assert received["Date"] is not None

        assert not sender.find_sent(message_id), "Rackspace files no copy by itself"
        sender.save_sent_copy(KEVIN_EMAIL, {}, message_id)
        assert sender.find_sent(message_id)
        assert subjects_in(env.server, AGENT, "Sent") == [KEVIN_EMAIL.subject]
        assert not sender.find_sent("<never-sent@icon-technologies.com>")

    def test_attachments_and_reply_headers_travel(self, env: Env) -> None:
        from finance_ops_agent.domain.emails import EmailAttachment

        email = OutgoingEmail(
            to=(KEVIN,),
            cc=(AGENT,),
            reply_to=KEVIN,
            in_reply_to="<question@icon-technologies.com>",
            subject="Re: a question",
            body="See attached.",
            attachments=(EmailAttachment("invoice.pdf", "a" * 64),),
        )
        env.sender().send(
            email, {"invoice.pdf": b"%PDF-1.4 invoice"}, "<answer@icon-technologies.com>"
        )
        [received] = messages_in(env.server, KEVIN)
        assert received["In-Reply-To"] == "<question@icon-technologies.com>"
        assert received["References"] == "<question@icon-technologies.com>"
        assert received["Reply-To"] == KEVIN
        [attachment] = list(received.iter_attachments())
        assert attachment.get_filename() == "invoice.pdf"
        assert attachment.get_content_type() == "application/pdf"
        assert attachment.get_payload(decode=True) == b"%PDF-1.4 invoice"

    def test_through_the_run_the_id_is_written_before_and_accepted_after(self, env: Env) -> None:
        env.readings["timesheet.pdf"] = reading(*AUG)
        deliver(env.server, timesheet_email())
        report = env.run()
        assert report.emails_sent >= 1

        records = [r for r in env.store.outgoing_records() if r.kind.endswith("_email")]
        assert records and all(r.status == "done" for r in records)
        for record in records:
            assert record.message_id and record.started_at and record.accepted_at
            assert record.started_at <= record.accepted_at
        # Kevin got each one, and each has its copy in Sent.
        got = {str(m["Message-ID"]) for m in messages_in(env.server, KEVIN)}
        kept = {str(m["Message-ID"]) for m in messages_in(env.server, AGENT, "Sent")}
        assert {r.message_id for r in records} <= got
        assert {r.message_id for r in records} <= kept

    def test_reconcile_found_in_sent_is_done(self, env: Env) -> None:
        message_id = "<found@icon-technologies.com>"
        env.sender().save_sent_copy(
            KEVIN_EMAIL, {}, message_id
        )  # the copy got filed; the write did not
        _record_in_flight(
            env.store, "billing:1", KEVIN_EMAIL, message_id, env.now - timedelta(hours=1)
        )

        send_pending(env.deps(), RunReport())

        [record] = env.store.outgoing_records()
        assert record.status == "done" and record.accepted_at is not None
        assert messages_in(env.server, KEVIN) == [], "not sent again"

    def test_reconcile_too_young_waits(self, env: Env) -> None:
        _record_in_flight(
            env.store,
            "billing:1",
            KEVIN_EMAIL,
            "<young@icon-technologies.com>",
            env.now - timedelta(minutes=3),
        )
        send_pending(env.deps(), RunReport())
        [record] = env.store.outgoing_records()
        assert record.status == "in_flight"
        assert env.store.open_reviews() == []
        assert messages_in(env.server, KEVIN) == []

    def test_reconcile_old_and_missing_asks_kevin_and_resend_reuses_the_id(self, env: Env) -> None:
        message_id = "<lost@icon-technologies.com>"
        _record_in_flight(
            env.store, "billing:1", KEVIN_EMAIL, message_id, env.now - timedelta(minutes=30)
        )

        send_pending(env.deps(), RunReport())
        assert [r.code for r in env.store.open_reviews()] == ["SEND_UNCERTAIN"]
        [question] = messages_in(env.server, KEVIN)
        assert str(question["Subject"]).startswith("Needs your review")
        assert 'reply "received" if you got it, or "resend"' in question.get_content()

        # Kevin replies "resend" to the question (his mail program sets In-Reply-To).
        answer = EmailMessage()
        answer["From"] = KEVIN
        answer["To"] = AGENT
        answer["Subject"] = "Re: " + str(question["Subject"])
        answer["In-Reply-To"] = str(question["Message-ID"])
        answer["Message-ID"] = "<kevin-1@icon-technologies.com>"
        answer.set_content("resend")
        deliver(env.server, answer)
        env.run()

        billing = next(r for r in env.store.outgoing_records() if r.kind == "billing_email")
        assert billing.status == "done" and billing.message_id == message_id
        resent = [m for m in messages_in(env.server, KEVIN) if m["Message-ID"] == message_id]
        assert len(resent) == 1, "sent once, under the Message-ID written down the first time"
        assert env.sender().find_sent(message_id)
        assert env.store.open_reviews() == []

    def test_reconcile_received_closes_it_without_sending(self, env: Env) -> None:
        message_id = "<lost@icon-technologies.com>"
        _record_in_flight(
            env.store, "billing:1", KEVIN_EMAIL, message_id, env.now - timedelta(minutes=30)
        )
        send_pending(env.deps(), RunReport())
        [question] = messages_in(env.server, KEVIN)

        answer = EmailMessage()
        answer["From"] = KEVIN
        answer["To"] = AGENT
        answer["Subject"] = "Re: " + str(question["Subject"])
        answer["In-Reply-To"] = str(question["Message-ID"])
        answer["Message-ID"] = "<kevin-1@icon-technologies.com>"
        answer.set_content("Received - thanks")
        deliver(env.server, answer)
        env.run()

        billing = next(r for r in env.store.outgoing_records() if r.kind == "billing_email")
        assert billing.status == "done"
        assert not any(m["Message-ID"] == message_id for m in messages_in(env.server, KEVIN))
        assert env.store.open_reviews() == []

    def test_a_rejected_recipient_is_final(self, env: Env) -> None:
        class RefusingSmtp(smtplib.SMTP):
            def send_message(self, *args: object, **kwargs: object) -> dict[str, object]:  # type: ignore[override]
                raise smtplib.SMTPRecipientsRefused({"nobody@acme.example": (550, b"no such user")})

        def connect(account: MailAccount) -> smtplib.SMTP:
            return RefusingSmtp(
                account.smtp_host, account.smtp_port, local_hostname="localhost", timeout=10
            )

        sender = SmtpSender(env.server.account(), AGENT, lambda: env.now, connect_smtp=connect)
        email = OutgoingEmail(to=("nobody@acme.example",), subject="Invoice", body="x")
        with pytest.raises(RecipientRefused, match="nobody@acme.example"):
            sender.send(email, {}, "<refused@icon-technologies.com>")

        # Through the application: SEND_FAILED at once, not three attempts.
        assert env.store.record_outgoing("billing_email", "billing:1", None, email.payload())
        deps = env.deps()
        deps.sender = sender
        send_pending(deps, RunReport())
        [record] = env.store.outgoing_records()
        assert record.status == "failed" and record.attempts == 1
        assert [r.code for r in env.store.open_reviews()] == ["SEND_FAILED"]

    def test_a_server_that_cannot_be_reached_is_not_sent(self, env: Env) -> None:
        unreachable = env.server.account(smtp_port=1)  # nothing listens there
        with pytest.raises(NotSent, match="could not reach"):
            SmtpSender(unreachable, AGENT, lambda: env.now).send(KEVIN_EMAIL, {}, "<x@y>")
        assert messages_in(env.server, KEVIN) == []

    def test_an_email_over_the_size_limit_is_refused_before_sending(self, env: Env) -> None:
        from finance_ops_agent.domain.emails import EmailAttachment

        email = OutgoingEmail(
            to=(KEVIN,),
            subject="Big",
            body="x",
            attachments=(EmailAttachment("big.bin", "b" * 64),),
        )
        with pytest.raises(NotSent, match="20 MB"):
            env.sender().send(email, {"big.bin": b"0" * (21 * 1024 * 1024)}, "<big@y>")
        assert messages_in(env.server, KEVIN) == []


class TestTls:
    def test_verification_is_on(self) -> None:
        context = tls_context()
        assert context.check_hostname
        assert context.verify_mode is ssl.CERT_REQUIRED

    def test_ssl_is_really_ssl(self, mailbox: MailServer) -> None:
        """Asking for `ssl` against a plain-text port fails instead of quietly
        falling back to plain text."""
        with pytest.raises(MailboxProblem):
            open_imap(mailbox.account(imap_security="ssl"))

    def test_a_wrong_password_is_a_mailbox_problem(self, mailbox: MailServer) -> None:
        with pytest.raises(MailboxProblem, match="refused the login"):
            open_imap(mailbox.account(password="wrong"))


# --- Kevin's replies -------------------------------------------------------


def _ask_kevin_about_hours(env: Env) -> EmailMessage:
    dailies = [(date(2026, 8, 3), 800), (date(2026, 8, 4), 800)]
    env.readings["timesheet.pdf"] = reading(*AUG, total_hundredths=15_600, dailies=dailies)
    deliver(env.server, timesheet_email())
    env.run()
    assert env.store.list_items()[0].status is ItemStatus.NEEDS_REVIEW
    return next(
        m
        for m in messages_in(env.server, KEVIN)
        if str(m["Subject"]).startswith("Needs your review")
    )


def _kevin_says(env: Env, body: str, subject: str, in_reply_to: str | None) -> None:
    env.replies[body] = ReplyReading(
        answers=[
            ReplyAnswer(
                review_code="HOURS_DONT_ADD_UP", kind=ReplyAnswerKind.HOURS, value="152", quote=body
            )
        ]
    )
    answer = EmailMessage()
    answer["From"] = KEVIN
    answer["To"] = AGENT
    answer["Subject"] = subject
    if in_reply_to:
        answer["In-Reply-To"] = in_reply_to
        answer["References"] = in_reply_to
    answer["Message-ID"] = make_msgid(domain="icon-technologies.com")
    answer.set_content(body)
    deliver(env.server, answer)


class TestKevinsReplies:
    def test_matched_by_in_reply_to_even_with_a_changed_subject(self, env: Env) -> None:
        question = _ask_kevin_about_hours(env)
        _kevin_says(env, "use 152 hours", "quick answer", str(question["Message-ID"]))
        env.run()
        item = env.store.list_items()[0]
        assert item.status is ItemStatus.READY
        assert str(item.approved_hours) == "152.00"

    def test_matched_by_subject_when_the_headers_are_missing(self, env: Env) -> None:
        question = _ask_kevin_about_hours(env)
        _kevin_says(env, "use 152 hours", "RE: " + str(question["Subject"]), None)
        env.run()
        assert env.store.list_items()[0].status is ItemStatus.READY

    def test_neither_means_the_reply_is_noted_and_the_item_waits(self, env: Env) -> None:
        _ask_kevin_about_hours(env)
        _kevin_says(env, "use 152 hours", "something else entirely", None)
        report = env.run()
        assert env.store.list_items()[0].status is ItemStatus.NEEDS_REVIEW
        assert any("couldn't match" in line for line in report.lines)


# --- fops doctor -----------------------------------------------------------

Capture = pytest.CaptureFixture[str]


class TestDoctor:
    @pytest.fixture
    def configured(
        self, monkeypatch: pytest.MonkeyPatch, mailbox: MailServer, tmp_path: Path
    ) -> MailServer:
        from tests.contract.test_engagement_list import build_xlsx

        build_xlsx(tmp_path / "engagements.xlsx")
        for name, value in {
            "FOPS_MODE": "dry_run",
            "FOPS_TIMEZONE": "America/New_York",
            "FOPS_ENGAGEMENT_LIST": str(tmp_path / "engagements.xlsx"),
            "FOPS_ADMIN_EMAIL": KEVIN,
            "FOPS_AGENT_MAILBOX": AGENT,
            "FOPS_DATA_DIR": str(tmp_path / "data"),
            "FOPS_ACCOUNTING": "manual",
            "MAIL_IMAP_HOST": mailbox.host,
            "MAIL_IMAP_PORT": str(mailbox.imap_port),
            "MAIL_IMAP_SECURITY": "none",
            "MAIL_SMTP_HOST": mailbox.host,
            "MAIL_SMTP_PORT": str(mailbox.smtp_port),
            "MAIL_SMTP_SECURITY": "none",
            "MAIL_USERNAME": AGENT,
            "MAIL_PASSWORD": "not-a-real-password",
            "MAIL_START_DATE": "2026-09-09",
        }.items():
            monkeypatch.setenv(name, value)
        monkeypatch.delenv("MAIL_SENT_FOLDER", raising=False)
        # This suite proves the mailbox against a real server on this machine.
        # The Claude check talks to a different service altogether, so it is
        # stubbed here rather than reaching the internet from a contract test.
        monkeypatch.setattr(
            "finance_ops_agent.cli.doctor._describe_claude_model",
            lambda model: f"Claude Opus 5 ({model}) answers; timesheets can be read",
        )
        return mailbox

    def test_a_missing_claude_key_fails_the_doctor(
        self,
        configured: MailServer,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A perfect mailbox is not enough: with no key, no timesheet can be read."""
        from finance_ops_agent.cli import doctor as doctor_module
        from finance_ops_agent.cli.main import main

        # Undo the fixture's stub: this test wants the real check, with no key.
        monkeypatch.setattr(doctor_module, "_describe_claude_model", _real_describe_claude_model)
        for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
            monkeypatch.delenv(name, raising=False)

        assert main(["doctor"]) == 1
        out = capsys.readouterr().out
        assert "FAIL claude api" in out
        assert "ok   read the mailbox (imap)" in out

    def test_everything_checks_out(
        self, configured: MailServer, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from finance_ops_agent.cli.main import main

        assert main(["doctor"]) == 0
        out = capsys.readouterr().out
        assert "reading mail from 2026-09-09" in out
        assert "PLAIN TEXT, test server only" in out
        assert "logged in; 0 message(s) in the inbox" in out
        assert "folders ready: Agent.Processed, Agent.Needs Review, Agent.Ignored" in out
        assert "sent copies go to Sent" in out
        assert "send mail (smtp)" in out
        assert "Everything checks out. Nothing was sent to a client." in out
        assert messages_in(configured, KEVIN) == []

    def test_the_test_email_reaches_kevin_and_sent(
        self, configured: MailServer, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from finance_ops_agent.cli.main import main

        assert main(["doctor", "--send-test-email"]) == 0
        out = capsys.readouterr().out
        assert "test email" in out
        assert subjects_in(configured, KEVIN) == ["fops doctor: this mailbox works"]
        assert subjects_in(configured, AGENT, "Sent") == ["fops doctor: this mailbox works"]

    def test_a_wrong_password_fails_the_mailbox_checks(
        self,
        configured: MailServer,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from finance_ops_agent.cli.main import main

        monkeypatch.setenv("MAIL_PASSWORD", "wrong")
        assert main(["doctor"]) == 1
        out = capsys.readouterr().out
        assert "FAIL read the mailbox (imap): the mailbox refused the login" in out
        assert "Nothing was sent to a client." in out

    def test_kevins_credentials_are_refused(
        self,
        configured: MailServer,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from finance_ops_agent.cli.main import main

        monkeypatch.setenv("MAIL_USERNAME", KEVIN)
        assert main(["doctor"]) == 1
        assert "the agent must log in to its own mailbox" in capsys.readouterr().out
