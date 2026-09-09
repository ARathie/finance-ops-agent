"""Raw email bytes to InboundEmail: the Message-ID rules and the attachment rules.

These run with no server; the same parser serves the IMAP adapter and the
fake mailbox, so the two can never disagree about what an email contains.
"""

from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import make_msgid

from finance_ops_agent.adapters.email.parse import parse_message
from finance_ops_agent.domain.messages import (
    SYNTHETIC_DOMAIN,
    normalise_message_id,
    parse_references,
    synthetic_message_id,
)


def _email(
    message_id: str | None = "<one@example>",
    body: str = "Hello",
    html: str | None = None,
    date: str | None = "Tue, 08 Sep 2026 09:00:00 -0400",
) -> EmailMessage:
    message = EmailMessage()
    message["From"] = "Priya Shah <priya@example.com>"
    message["To"] = "jay@icon-technologies.com"
    message["Subject"] = "August timesheet"
    if date:
        message["Date"] = date
    if message_id:
        message["Message-ID"] = message_id
    if html is not None:
        message.set_content(body)
        message.add_alternative(html, subtype="html")
    else:
        message.set_content(body)
    return message


class TestMessageIds:
    def test_normalised_with_brackets_and_folding_removed(self) -> None:
        assert normalise_message_id(" <a@b> ") == "<a@b>"
        assert normalise_message_id("a@b") == "<a@b>"
        assert normalise_message_id("<a@\r\n b>") == "<a@ b>"
        assert normalise_message_id(None) == ""
        assert normalise_message_id("   ") == ""

    def test_references_are_every_bracketed_id(self) -> None:
        assert parse_references("<a@b> <c@d>\r\n <e@f>") == ("<a@b>", "<c@d>", "<e@f>")
        assert parse_references(None) == ()
        assert parse_references("junk without brackets") == ()

    def test_a_synthetic_id_is_stable_and_marked_as_such(self) -> None:
        first = synthetic_message_id("Priya@Example.com", "date", "Subject", "body")
        again = synthetic_message_id("priya@example.com ", "date", "Subject ", " body")
        other = synthetic_message_id("priya@example.com", "date", "Subject", "different body")
        assert first == again
        assert first != other
        assert first.startswith("<synthetic-") and first.endswith(f"@{SYNTHETIC_DOMAIN}>")

    def test_parsed_from_the_header_or_made_up(self) -> None:
        assert parse_message(bytes(_email())).email.message_id == "<one@example>"
        made_up = parse_message(bytes(_email(message_id=None))).email.message_id
        assert made_up.startswith("<synthetic-")
        assert parse_message(bytes(_email(message_id=None))).email.message_id == made_up

    def test_reply_headers(self) -> None:
        message = _email()
        message["In-Reply-To"] = "<ask@icon-technologies.com>"
        message["References"] = "<first@icon-technologies.com> <ask@icon-technologies.com>"
        email = parse_message(bytes(message)).email
        assert email.in_reply_to == "<ask@icon-technologies.com>"
        assert email.references == ("<first@icon-technologies.com>", "<ask@icon-technologies.com>")
        assert parse_message(bytes(_email())).email.in_reply_to == ""


class TestHeadersAndBody:
    def test_addresses_subject_and_date(self) -> None:
        email = parse_message(bytes(_email())).email
        assert email.from_address == "priya@example.com"  # the bare address, no display name
        assert email.to_addresses == "jay@icon-technologies.com"
        assert email.subject == "August timesheet"
        assert email.received_at == datetime(2026, 9, 8, 13, 0, tzinfo=UTC)

    def test_the_date_falls_back_to_the_servers_time(self) -> None:
        arrived = datetime(2026, 9, 9, 8, 30, tzinfo=UTC)
        email = parse_message(bytes(_email(date=None)), arrived).email
        assert email.received_at == arrived
        naive = parse_message(bytes(_email(date=None)), arrived.replace(tzinfo=None)).email
        assert naive.received_at == arrived
        assert parse_message(bytes(_email(date=None))).email.received_at.year == 1970

    def test_plain_text_is_preferred_and_html_is_stripped(self) -> None:
        both = parse_message(bytes(_email(body="plain words", html="<p>rich</p>"))).email
        assert both.body_text.strip() == "plain words"
        message = EmailMessage()
        message["From"] = "priya@example.com"
        message["Message-ID"] = "<html@example>"
        message.set_content(
            "<html><body><p>Approved</p><p>by <b>Jane</b></p></body></html>", subtype="html"
        )
        html_only = parse_message(bytes(message)).email
        assert html_only.body_text == "Approved by Jane"


class TestAttachments:
    def test_regular_attachments_with_content(self) -> None:
        message = _email()
        message.add_attachment(b"%PDF", maintype="application", subtype="pdf", filename="ts.pdf")
        message.add_attachment(b"XLS", maintype="application", subtype="octet-stream")
        parsed = parse_message(bytes(message))
        names = [(a.filename, a.mime_type, a.size_bytes) for a in parsed.email.attachments]
        assert names == [
            ("ts.pdf", "application/pdf", 4),
            ("attachment-1", "application/octet-stream", 3),
        ]
        first, second = parsed.email.attachments
        assert first.attachment_id == "<one@example>:0"
        assert parsed.attachment_content == {
            first.attachment_id: b"%PDF",
            second.attachment_id: b"XLS",
        }

    def test_inline_images_are_ignored_next_to_a_real_attachment(self) -> None:
        message = _email()
        message.add_attachment(
            b"PNG",
            maintype="image",
            subtype="png",
            filename="sig.png",
            disposition="inline",
            cid=make_msgid(domain="example"),
        )
        message.add_attachment(b"%PDF", maintype="application", subtype="pdf", filename="ts.pdf")
        parsed = parse_message(bytes(message))
        assert [a.filename for a in parsed.email.attachments] == ["ts.pdf"]
        assert list(parsed.attachment_content.values()) == [b"%PDF"]

    def test_one_inline_image_counts_when_it_is_all_there_is(self) -> None:
        """A timesheet pasted into the email body is an inline image."""
        message = _email()
        for name in ("pasted.png", "second.png"):
            message.add_attachment(
                b"PNG",
                maintype="image",
                subtype="png",
                filename=name,
                disposition="inline",
                cid=make_msgid(domain="example"),
            )
        parsed = parse_message(bytes(message))
        assert [a.filename for a in parsed.email.attachments] == ["pasted.png"]

    def test_an_image_sent_as_an_attachment_is_a_real_attachment(self) -> None:
        message = _email()
        message.add_attachment(b"JPG", maintype="image", subtype="jpeg", filename="photo.jpg")
        parsed = parse_message(bytes(message))
        assert [a.filename for a in parsed.email.attachments] == ["photo.jpg"]

    def test_no_attachments(self) -> None:
        parsed = parse_message(bytes(_email()))
        assert parsed.email.attachments == ()
        assert parsed.attachment_content == {}
