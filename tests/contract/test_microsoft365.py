"""The Microsoft 365 adapter against recorded Graph responses.

Every case the roadmap names for PR 8: delta paging and expiry, attachment
download, draft + attachments + send, 429 backoff, and reconciling an
in-flight draft — plus the doctor's proof that the app cannot read anyone
else's mailbox. No network, no credentials.
"""

from datetime import UTC, datetime

import pytest

from finance_ops_agent.adapters.microsoft365.client import (
    GraphClient,
    MailboxProblem,
)
from finance_ops_agent.adapters.microsoft365.inbox import GraphInbox
from finance_ops_agent.adapters.microsoft365.sender import GraphSender
from finance_ops_agent.cli.doctor import (
    CheckResult,
    check_can_read_the_agent_mailbox,
    check_cannot_read_another_mailbox,
    check_token,
)
from finance_ops_agent.domain.emails import EmailAttachment, OutgoingEmail
from finance_ops_agent.ports.sender import DraftState
from tests.contract.graph_replay import Replay

MAILBOX = "jay@icon-technologies.com"
KEVIN = "kevin@icon-technologies.com"


def build(replay: Replay) -> tuple[GraphClient, list[float]]:
    slept: list[float] = []
    client = GraphClient(
        MAILBOX,
        get_token=lambda: "a-test-token",
        http=replay.client(),
        sleep=slept.append,
    )
    return client, slept


class TestDelta:
    def test_paging_follows_next_link_and_returns_the_delta_link(self) -> None:
        replay = Replay.from_files("delta_paging")
        client, _ = build(replay)
        emails, cursor = GraphInbox(client).new_messages(None)

        assert [email.provider_id for email in emails] == ["AAA1", "AAA2"]  # the draft is skipped
        assert cursor.endswith("$deltatoken=LATEST")
        first = emails[0]
        assert first.from_address == "priya@example.com"
        assert first.subject == "August timesheet"
        assert first.internet_message_id == "<AAA1@example>"
        assert first.received_at == datetime(2026, 9, 2, 9, 12, tzinfo=UTC)
        assert "Thanks, Priya" in first.body_text

    def test_an_inline_signature_image_is_not_mistaken_for_the_timesheet(self) -> None:
        replay = Replay.from_files("delta_paging")
        client, _ = build(replay)
        emails, _ = GraphInbox(client).new_messages(None)
        [attachment] = emails[0].attachments
        assert attachment.filename == "priya-august.pdf"

    def test_an_expired_delta_link_starts_again_instead_of_failing(self) -> None:
        replay = Replay.from_files("delta_expiry")
        client, _ = build(replay)
        saved = (
            "https://graph.microsoft.com/v1.0/users/jay@icon-technologies.com"
            "/mailFolders/inbox/messages/delta?$deltatoken=OLD"
        )
        emails, cursor = GraphInbox(client).new_messages(saved)

        assert [email.provider_id for email in emails] == ["AAA1"]
        assert cursor.endswith("$deltatoken=LATEST")
        assert any("$deltatoken=OLD" in url for url in replay.urls())  # it did try the old one

    def test_immutable_ids_are_asked_for_on_every_call(self) -> None:
        replay = Replay.from_files("delta_paging")
        client, _ = build(replay)
        GraphInbox(client).new_messages(None)

        assert replay.headers, "the adapter made no calls"
        for headers in replay.headers:
            # Without this, ids change when a message is moved to a folder and
            # the agent would reprocess mail it had already handled.
            assert 'IdType="ImmutableId"' in headers.get("prefer", "")

    def test_the_body_is_asked_for_as_plain_text(self) -> None:
        replay = Replay.from_files("delta_paging")
        client, _ = build(replay)
        GraphInbox(client).new_messages(None)
        body_calls = [
            headers
            for headers, (_, url) in zip(replay.headers, replay.calls, strict=True)
            if "$select=body" in url
        ]
        assert body_calls
        for headers in body_calls:
            assert 'outlook.body-content-type="text"' in headers.get("prefer", "")


class TestAttachments:
    def test_download(self) -> None:
        replay = Replay.from_files("attachment_download")
        client, _ = build(replay)
        content = GraphInbox(client).download_attachment("AAA1", "ATT1")
        assert content.startswith(b"%PDF-1.4")


class TestSending:
    def test_draft_then_attachments_then_send_in_that_order(self) -> None:
        replay = Replay.from_files("draft_and_send")
        client, _ = build(replay)
        sender = GraphSender(client)

        email = OutgoingEmail(
            to=("ap@acme.example",),
            cc=(KEVIN,),
            reply_to=KEVIN,
            subject="Icon Technologies invoice ICON-2026-1001",
            body="Please find attached.",
            attachments=(EmailAttachment("invoice.pdf", "a" * 64),),
        )
        draft_id = sender.create_draft(email, {"invoice.pdf": b"%PDF-fake"})
        assert draft_id == "DRAFT-9"
        sender.send(draft_id)

        methods_and_paths = [(method, url.rsplit("/", 1)[-1]) for method, url in replay.calls]
        assert methods_and_paths == [
            ("POST", "messages"),
            ("POST", "attachments"),
            ("POST", "send"),
        ], "the draft must exist before the attachment, and both before the send"

    def test_the_draft_carries_recipients_cc_and_reply_to(self) -> None:
        replay = Replay.from_files("draft_and_send")
        client, _ = build(replay)
        GraphSender(client).create_draft(
            OutgoingEmail(
                to=("ap@acme.example",),
                cc=(KEVIN,),
                reply_to=KEVIN,
                subject="Invoice",
                body="Hello",
            ),
            {},
        )
        draft = replay.bodies[0]
        assert draft["toRecipients"] == [{"emailAddress": {"address": "ap@acme.example"}}]
        assert draft["ccRecipients"] == [{"emailAddress": {"address": KEVIN}}]
        assert draft["replyTo"] == [{"emailAddress": {"address": KEVIN}}]
        assert draft["body"]["contentType"] == "text"

    def test_attachment_content_is_base64(self) -> None:
        import base64

        replay = Replay.from_files("draft_and_send")
        client, _ = build(replay)
        GraphSender(client).create_draft(
            OutgoingEmail(
                to=(KEVIN,),
                subject="Invoice",
                body="Hello",
                attachments=(EmailAttachment("invoice.pdf", "a" * 64),),
            ),
            {"invoice.pdf": b"%PDF-fake"},
        )
        attachment = replay.bodies[1]
        assert attachment["@odata.type"] == "#microsoft.graph.fileAttachment"
        assert base64.standard_b64decode(attachment["contentBytes"]) == b"%PDF-fake"


class TestReconcile:
    """The restart question: what really happened to this draft?"""

    @pytest.mark.parametrize(
        ("draft_id", "expected"),
        [
            ("SENT-1", DraftState.SENT),
            ("DRAFT-9", DraftState.STILL_DRAFT),
            ("GONE-1", DraftState.MISSING),
        ],
    )
    def test_states(self, draft_id: str, expected: DraftState) -> None:
        replay = Replay.from_files("reconcile")
        client, _ = build(replay)
        assert GraphSender(client).find_draft(draft_id) is expected


class TestThrottling:
    def test_429_waits_for_retry_after_and_then_succeeds(self) -> None:
        replay = Replay.from_files("throttled")
        client, slept = build(replay)
        emails, _ = GraphInbox(client).new_messages(None)

        assert slept == [7.0]  # exactly what Retry-After asked for
        assert [email.provider_id for email in emails] == ["AAA1"]

    def test_a_failure_that_keeps_happening_becomes_a_mailbox_problem(self) -> None:
        replay = Replay(
            {
                f"GET https://graph.microsoft.com/v1.0/users/{MAILBOX}/anything": [
                    {"status": 503, "json": {"error": {"code": "ServiceUnavailable"}}}
                ]
            }
        )
        client, slept = build(replay)
        with pytest.raises(MailboxProblem):
            client.get(client.user_url("/anything"))
        assert len(slept) == 2  # three tries, two waits


class TestFolders:
    def test_moving_a_message_creates_the_folder_if_it_is_missing(self) -> None:
        replay = Replay.from_files("move")
        client, _ = build(replay)
        GraphInbox(client).move("AAA2", "Needs Review")

        created = replay.bodies[0]
        assert created == {"displayName": "Needs Review"}
        assert replay.bodies[1] == {"destinationId": "REVIEWFOLDER"}


class TestDoctor:
    def test_a_correctly_scoped_app_passes_every_check(self) -> None:
        replay = Replay.from_files("doctor_scoped")
        client, _ = build(replay)

        assert check_token(client).result is CheckResult.PASS
        assert check_can_read_the_agent_mailbox(client).result is CheckResult.PASS
        scoping = check_cannot_read_another_mailbox(client, KEVIN)
        assert scoping.result is CheckResult.PASS
        assert "denied" in scoping.detail

    def test_an_app_that_can_read_kevins_mailbox_fails_the_doctor(self) -> None:
        replay = Replay.from_files("doctor_unscoped")
        client, _ = build(replay)
        scoping = check_cannot_read_another_mailbox(client, KEVIN)

        assert scoping.result is CheckResult.FAIL
        assert "application access policy" in scoping.detail

    def test_with_no_other_mailbox_to_test_the_check_fails_rather_than_passing(self) -> None:
        replay = Replay.from_files("doctor_scoped")
        client, _ = build(replay)
        assert check_cannot_read_another_mailbox(client, "").result is CheckResult.FAIL
