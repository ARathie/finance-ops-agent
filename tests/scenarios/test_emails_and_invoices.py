"""Whole runs that send: the modes, Kevin's replies, and never-twice sending."""

from dataclasses import replace as dc_replace
from datetime import date, timedelta

import pytest

from finance_ops_agent.adapters.fakes.sender import FakeSender, SimulatedCrash
from finance_ops_agent.application.context import Mode
from finance_ops_agent.application.run import run_once
from finance_ops_agent.domain.emails import OutgoingEmail
from finance_ops_agent.domain.items import OutgoingRecord
from finance_ops_agent.domain.money import Money
from finance_ops_agent.domain.reading import ReplyAnswer, ReplyAnswerKind, ReplyReading
from finance_ops_agent.domain.statuses import ItemStatus
from finance_ops_agent.ports.sender import NotSent, RecipientRefused
from tests.scenarios.conftest import PRIYA, ScenarioEnv, reading

AUG_START, AUG_END = date(2026, 8, 1), date(2026, 8, 31)


def clean_timesheet(env: ScenarioEnv) -> None:
    env.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END))


def kinds_sent(env: ScenarioEnv) -> list[str]:
    return [record.kind for record in env.store.outgoing_records() if record.status == "done"]


class TestDryRun:
    def test_nothing_goes_to_the_client(self, env: ScenarioEnv) -> None:
        clean_timesheet(env)
        env.run()

        for email in env.sender.sent_emails():
            assert email.to == ("kevin@icon-technologies.com",)
        assert env.the_item().status is ItemStatus.READY
        assert env.store.invoices_for_item(env.the_item().id) == []

    def test_no_address_but_kevins_is_ever_written_to(self, env: ScenarioEnv) -> None:
        """The guarantee the first cycle rests on: while the mode is dry run,
        the only person the agent can write to is Kevin. On every path, and on
        CC as well as To -- the client's address is on the engagement list the
        whole time and still hears nothing.
        """
        clean_timesheet(env)
        env.add_email("newsletter@conference.example", subject="Speaker invitation")
        env.run()
        env.add_email(
            PRIYA,
            subject="Corrected timesheet",
            attachment=("timesheet-v2.pdf", b"PDFDATA2"),
            scripted_reading=reading(AUG_START, AUG_END, total_hundredths=15_000),
        )
        env.run()

        written_to = {
            address for email in env.sender.sent_emails() for address in (*email.to, *email.cc)
        }
        assert written_to == {"kevin@icon-technologies.com"}
        assert env.sender.sent_emails(), "the run sent nothing, so this proves nothing"

    def test_kevin_sees_what_would_be_sent_and_what_is_owed(self, env: ScenarioEnv) -> None:
        clean_timesheet(env)
        env.run()
        subjects = env.sent_subjects()
        assert any("Dry run — would invoice Acme Corp" in s for s in subjects)
        assert any("Payment due 2026-09-15" in s and "$15,600.00" in s for s in subjects)
        assert any("Timesheet received" in s for s in subjects)

    def test_it_stays_a_dry_run_even_when_every_guardrail_would_pass(
        self, env: ScenarioEnv
    ) -> None:
        """The kill switch is checked before the guardrails, not after them."""
        from tests.scenarios.conftest import engagement_row

        env.workbook.engagements[0] = engagement_row(2, **{"Send automatically": "yes"})
        clean_timesheet(env)
        env.run()

        item = env.the_item()
        assert item.status is ItemStatus.READY  # not invoice_sent
        assert env.store.invoices_for_item(item.id) == []
        assert not env.accounting.invoices
        for email in env.sender.sent_emails():
            assert email.to == ("kevin@icon-technologies.com",)


class TestWhichFileIsAttached:
    """A consultant working through a firm sends the approved timesheet and the
    firm's invoice in one email, in whichever order they please. The file the
    agent attaches to its own emails must be the timesheet.

    The firm's invoice shows what Icon pays -- the pay rate -- so sending it to
    a client breaks rule 4. It reached one because the attachment was chosen by
    position: whichever file the email listed first.
    """

    def invoice_first(self, env: ScenarioEnv) -> None:
        env.add_email(
            PRIYA,
            attachments=[
                # The firm's invoice: a total, no approval, and a pay rate on it.
                ("vendor-invoice.pdf", b"INVOICE", reading(AUG_START, AUG_END, approved=False)),
                # The timesheet: what the client approved.
                ("timesheet.pdf", b"TIMESHEET", reading(AUG_START, AUG_END)),
            ],
        )

    def test_kevin_is_shown_the_timesheet_not_the_invoice(self, env: ScenarioEnv) -> None:
        self.invoice_first(env)
        env.run()

        details = [e for e in env.sender.sent_emails() if "Timesheet received" in e.subject]
        assert details, "no timesheet-received email"
        names = [a.filename for a in details[0].attachments]
        assert names == ["timesheet.pdf"], names

    def test_the_client_is_never_sent_the_firms_invoice(self, env: ScenarioEnv) -> None:
        env.mode = Mode.AUTO
        from tests.scenarios.conftest import engagement_row

        env.workbook.engagements[0] = engagement_row(2, **{"Send automatically": "yes"})
        self.invoice_first(env)
        env.run()

        billing = [e for e in env.sender.sent_emails() if e.to == ("ap@acme.example",)]
        assert billing, "the client was sent nothing to check"
        names = [a.filename for a in billing[0].attachments]
        assert "vendor-invoice.pdf" not in names, f"the pay rate went to the client: {names}"
        assert "timesheet.pdf" in names, names


class TestAskFirst:
    def test_kevin_is_asked_and_approving_sends_the_invoice(self, env: ScenarioEnv) -> None:
        env.mode = Mode.ASK_FIRST
        clean_timesheet(env)
        env.run()

        item = env.the_item()
        assert item.status is ItemStatus.WAITING_FOR_APPROVAL
        approval = next(s for s in env.sent_subjects() if s.startswith("Approve?"))
        assert "$21,840.00" in approval
        assert not any(email.to == ("ap@acme.example",) for email in env.sender.sent_emails())

        env.reply_from_kevin(approval, "approve")
        env.run()

        item = env.store.get_item(item.id)
        assert item.status is ItemStatus.INVOICE_SENT
        billing = [email for email in env.sender.sent_emails() if email.to == ("ap@acme.example",)]
        assert len(billing) == 1
        assert "kevin@icon-technologies.com" in billing[0].cc
        invoices = env.store.invoices_for_item(item.id)
        assert len(invoices) == 1
        assert invoices[0].amount_cents == Money(2_184_000).cents
        assert invoices[0].status == "sent"
        assert env.store.payment_instructions_for_item(item.id)

    def test_cancel_stops_it(self, env: ScenarioEnv) -> None:
        env.mode = Mode.ASK_FIRST
        clean_timesheet(env)
        env.run()
        approval = next(s for s in env.sent_subjects() if s.startswith("Approve?"))

        env.reply_from_kevin(approval, "cancel — she was on leave")
        env.run()

        assert env.the_item().status is ItemStatus.CANCELLED
        assert not any(email.to == ("ap@acme.example",) for email in env.sender.sent_emails())

    def test_anything_else_gets_a_short_reply_asking_for_one_of_the_two_words(
        self, env: ScenarioEnv
    ) -> None:
        env.mode = Mode.ASK_FIRST
        clean_timesheet(env)
        env.run()
        approval = next(s for s in env.sent_subjects() if s.startswith("Approve?"))

        env.reply_from_kevin(approval, "looks fine to me")
        env.run()

        assert env.the_item().status is ItemStatus.WAITING_FOR_APPROVAL
        assert any(
            "approve" in email.body and "cancel" in email.body for email in env.sender.sent_emails()
        )


class TestAutomatic:
    def test_only_engagements_marked_send_automatically(self, env: ScenarioEnv) -> None:
        env.mode = Mode.AUTO
        clean_timesheet(env)
        env.run()
        # The default engagement row says "Send automatically = no".
        assert env.the_item().status is ItemStatus.WAITING_FOR_APPROVAL

    def test_marked_engagements_send_without_asking(self, env: ScenarioEnv) -> None:
        from tests.scenarios.conftest import engagement_row

        env.mode = Mode.AUTO
        env.workbook.engagements[0] = engagement_row(2, **{"Send automatically": "yes"})
        clean_timesheet(env)
        env.run()

        item = env.the_item()
        assert item.status is ItemStatus.INVOICE_SENT
        billing = [email for email in env.sender.sent_emails() if email.to == ("ap@acme.example",)]
        assert len(billing) == 1
        assert "kevin@icon-technologies.com" in billing[0].cc

    def test_anything_less_than_certain_is_asked_about_instead(self, env: ScenarioEnv) -> None:
        """Medium confidence is good enough to work with, not to send unasked."""
        from finance_ops_agent.domain.reading import Confidence
        from tests.scenarios.conftest import engagement_row

        env.mode = Mode.AUTO
        env.workbook.engagements[0] = engagement_row(2, **{"Send automatically": "yes"})
        env.add_email(
            PRIYA,
            scripted_reading=reading(AUG_START, AUG_END, confidence=Confidence.MEDIUM),
        )
        report = env.run()

        assert env.the_item().status is ItemStatus.WAITING_FOR_APPROVAL
        assert any("not completely sure" in line for line in report.lines)
        assert not any(email.to == ("ap@acme.example",) for email in env.sender.sent_emails())

    def test_anything_open_for_kevin_makes_it_ask(self, env: ScenarioEnv) -> None:
        """Belt and braces: the flow already keeps an item out of ready while a
        review is open, so the review here is opened directly. The guardrail is
        the second lock on the same door, and it holds."""
        from tests.scenarios.conftest import engagement_row

        env.workbook.engagements[0] = engagement_row(2, **{"Send automatically": "yes"})
        clean_timesheet(env)
        env.run()  # dry run: the item becomes ready, nothing goes out
        item = env.the_item()
        assert item.status is ItemStatus.READY
        env.store.open_review(item.id, "NOT_SURE", "Something needs your eyes.")

        env.mode = Mode.AUTO
        report = env.run()

        assert env.store.get_item(item.id).status is ItemStatus.WAITING_FOR_APPROVAL
        assert any("need your review" in line for line in report.lines)
        assert not any(email.to == ("ap@acme.example",) for email in env.sender.sent_emails())

    def test_an_amount_unlike_the_recent_ones_makes_it_ask(self, env: ScenarioEnv) -> None:
        """The last invoice was for 156 hours; 40 is not a small difference."""
        from tests.scenarios.conftest import engagement_row

        env.mode = Mode.AUTO
        env.workbook.engagements[0] = engagement_row(2, **{"Send automatically": "yes"})
        clean_timesheet(env)
        env.run()
        assert env.the_item().status is ItemStatus.INVOICE_SENT

        env.today = date(2026, 10, 6)
        env.add_email(
            PRIYA,
            attachment=("timesheet-september.pdf", b"PDFDATA-SEP"),
            scripted_reading=reading(date(2026, 9, 1), date(2026, 9, 30), total_hundredths=4_000),
        )
        report = env.run()

        september = next(i for i in env.store.list_items() if i.period.start == date(2026, 9, 1))
        assert env.store.get_item(september.id).status is ItemStatus.WAITING_FOR_APPROVAL
        assert any("25% away from the recent invoices" in line for line in report.lines)
        assert env.store.invoices_for_item(september.id) == []


def _auto_clean_timesheet(env: ScenarioEnv) -> None:
    from tests.scenarios.conftest import engagement_row

    env.mode = Mode.AUTO
    env.workbook.engagements[0] = engagement_row(2, **{"Send automatically": "yes"})
    clean_timesheet(env)


def _billing_record(env: ScenarioEnv) -> OutgoingRecord:
    return next(record for record in env.store.outgoing_records() if record.kind == "billing_email")


def _question_about(env: ScenarioEnv, subject_part: str) -> str:
    return next(
        s
        for s in env.sent_subjects()
        if s.startswith("Needs your review") and subject_part in s and "I sent" in s
    )


def _billing_emails_sent(sender: FakeSender) -> list[OutgoingEmail]:
    return [email for email in sender.sent_emails() if email.to == ("ap@acme.example",)]


def _crash_on_the_billing_email(env: ScenarioEnv, **crash: bool) -> FakeSender:
    """Run until the crash lands on the billing email, which is the one that
    must never go out twice. Each run gets one email further."""
    crashing = FakeSender(env.sender.outbox, **crash)
    while True:
        with pytest.raises(SimulatedCrash):
            run_once(dc_replace(env.deps(), sender=crashing))
        billing = next((r for r in env.store.outgoing_records() if r.kind == "billing_email"), None)
        if billing is not None and billing.status == "in_flight":
            return crashing


class TestNeverTwice:
    """docs/integrations/email-imap-smtp.md, "Never twice": the Message-ID is
    written down before the send; after a crash the agent checks the Sent
    folder, waits ten minutes, and then asks Kevin rather than guessing."""

    def test_a_crash_before_the_server_took_it_is_simply_retried(self, env: ScenarioEnv) -> None:
        _auto_clean_timesheet(env)
        crashing = _crash_on_the_billing_email(env, crash_before_send=True)
        assert _billing_emails_sent(crashing) == []
        billing = _billing_record(env)
        assert billing.message_id is not None, "the Message-ID was written down first"

        # Restart: nothing in Sent, but the record is fresh, so the agent waits...
        env.run()
        assert _billing_record(env).status == "in_flight"
        assert _billing_emails_sent(env.sender) == []

        # ...and once ten minutes have passed it asks Kevin instead of guessing.
        env.now = env.deps().clock.now() + timedelta(minutes=11)
        env.run()
        assert "SEND_UNCERTAIN" in {review.code for review in env.store.open_reviews()}
        question = _question_about(env, "invoice FAKE-1")
        assert _billing_emails_sent(env.sender) == []

        # Kevin never got it and says so: the same email goes out under the same Message-ID.
        env.reply_from_kevin(question, "resend")
        env.run()
        assert _billing_record(env).status == "done"
        assert _billing_record(env).message_id == billing.message_id
        assert len(_billing_emails_sent(env.sender)) == 1
        # Only the billing question was answered; the details email is still in doubt.
        still_open = [r for r in env.store.open_reviews() if r.code == "SEND_UNCERTAIN"]
        assert len(still_open) == 1 and "Timesheet received" in still_open[0].message

    def test_a_crash_after_the_server_took_it_results_in_exactly_one_email(
        self, env: ScenarioEnv
    ) -> None:
        _auto_clean_timesheet(env)
        crashing = _crash_on_the_billing_email(env, crash_after_send=True)
        assert len(_billing_emails_sent(crashing)) == 1, "the server really did take it"
        billing = _billing_record(env)
        assert billing.status == "in_flight" and billing.accepted_at is None

        # Restart on the server's state: Rackspace keeps no copy in Sent by
        # itself, so the agent cannot tell yet; it waits ten minutes, then asks.
        recovered = FakeSender(env.sender.outbox)
        recovered.sent, recovered.emails = crashing.sent, crashing.emails
        env.sender = recovered
        env.run()
        assert _billing_record(env).status == "in_flight"
        env.now = env.deps().clock.now() + timedelta(minutes=11)
        env.run()
        question = _question_about(env, "invoice FAKE-1")
        assert "SEND_UNCERTAIN" in {review.code for review in env.store.open_reviews()}

        # Kevin was on CC and got it.
        env.reply_from_kevin(question, "Received, thanks")
        env.run()
        assert _billing_record(env).status == "done"
        assert len(_billing_emails_sent(recovered)) == 1, "the billing email went out exactly once"
        assert not any("invoice FAKE-1" in r.message for r in env.store.open_reviews())
        assert not any("Needs your review" in s for s in env.sent_subjects()[-1:])

    def test_a_copy_in_sent_settles_it_without_asking(self, env: ScenarioEnv) -> None:
        """A server that files its own Sent copy (or the copy the agent filed
        before dying) answers the question, so Kevin is never bothered."""
        _auto_clean_timesheet(env)
        crashing = _crash_on_the_billing_email(env, crash_after_send=True, provider_saves_sent=True)

        recovered = FakeSender(env.sender.outbox)
        recovered.sent, recovered.emails = crashing.sent, crashing.emails
        recovered.sent_copies = crashing.sent_copies
        env.sender = recovered
        env.run()

        assert _billing_record(env).status == "done"
        assert len(_billing_emails_sent(recovered)) == 1
        assert not any(s.startswith("Needs your review") for s in env.sent_subjects())
        assert env.store.open_reviews() == []

    def test_an_answer_that_is_neither_word_is_asked_again(self, env: ScenarioEnv) -> None:
        _auto_clean_timesheet(env)
        _crash_on_the_billing_email(env, crash_before_send=True)
        env.now = env.deps().clock.now() + timedelta(minutes=11)
        env.run()
        question = _question_about(env, "invoice FAKE-1")

        env.reply_from_kevin(question, "not sure, let me check")
        env.run()
        assert _billing_record(env).status == "in_flight"
        assert any('"received" or "resend"' in email.body for email in env.sender.sent_emails())
        assert _billing_emails_sent(env.sender) == []

    def test_running_again_sends_nothing_new(self, env: ScenarioEnv) -> None:
        clean_timesheet(env)
        env.run()
        first = list(kinds_sent(env))
        sent_count = len(env.sender.sent)

        env.run()
        assert kinds_sent(env) == first
        assert len(env.sender.sent) == sent_count

    def test_a_send_that_keeps_failing_becomes_a_review(self, env: ScenarioEnv) -> None:
        clean_timesheet(env)

        class BrokenSender(FakeSender):
            def send(
                self, email: OutgoingEmail, attachments: dict[str, bytes], message_id: str
            ) -> None:
                raise NotSent("the mailbox is not answering")

        broken = BrokenSender(env.sender.outbox)
        for _ in range(3):
            with pytest.raises(NotSent):
                run_once(dc_replace(env.deps(), sender=broken))
            # The server took nothing, so the record goes straight back to
            # pending with one more attempt counted; the next run tries again.

        codes = {review.code for review in env.store.open_reviews()}
        assert "SEND_FAILED" in codes
        assert broken.sent == []

    def test_a_refused_address_stops_that_email_and_asks_kevin(self, env: ScenarioEnv) -> None:
        """A bad recipient is not worth retrying: the first refusal is final."""
        _auto_clean_timesheet(env)

        class RefusingSender(FakeSender):
            def send(
                self, email: OutgoingEmail, attachments: dict[str, bytes], message_id: str
            ) -> None:
                if email.to == ("ap@acme.example",):
                    raise RecipientRefused("550 no such user: ap@acme.example")
                super().send(email, attachments, message_id)

        refusing = RefusingSender(env.sender.outbox)
        run_once(dc_replace(env.deps(), sender=refusing))  # the run itself carries on

        assert _billing_record(env).status == "failed"
        assert _billing_record(env).attempts == 1
        assert "SEND_FAILED" in {review.code for review in env.store.open_reviews()}
        assert _billing_emails_sent(refusing) == []


class TestReviewReplies:
    def test_kevin_answers_the_hours_and_the_item_becomes_ready(self, env: ScenarioEnv) -> None:
        dailies = [(date(2026, 8, 3), 800), (date(2026, 8, 4), 800)]
        env.add_email(
            PRIYA,
            scripted_reading=reading(AUG_START, AUG_END, total_hundredths=15_600, dailies=dailies),
        )
        env.run()
        item = env.the_item()
        assert item.status is ItemStatus.NEEDS_REVIEW
        review_subject = next(s for s in env.sent_subjects() if s.startswith("Needs your review"))

        env.replies["use 152 hours"] = ReplyReading(
            answers=[
                ReplyAnswer(
                    review_code="HOURS_DONT_ADD_UP",
                    kind=ReplyAnswerKind.HOURS,
                    value="152",
                    quote="use 152 hours",
                )
            ]
        )
        env.reply_from_kevin(review_subject, "use 152 hours")
        env.run()

        item = env.store.get_item(item.id)
        assert item.status is ItemStatus.READY
        assert item.approved_hours is not None
        assert str(item.approved_hours) == "152.00"
        assert item.invoice_amount == Money(152 * 14_000)

    def test_ignore_closes_the_item(self, env: ScenarioEnv) -> None:
        env.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END, approved=False))
        env.run()
        item = env.the_item()
        review_subject = next(s for s in env.sent_subjects() if s.startswith("Needs your review"))

        env.replies["ignore"] = ReplyReading(
            answers=[ReplyAnswer(review_code="NO_APPROVAL", kind=ReplyAnswerKind.IGNORE)]
        )
        env.reply_from_kevin(review_subject, "ignore")
        env.run()

        assert env.store.get_item(item.id).status is ItemStatus.IGNORED
        assert env.store.open_reviews() == []

    def test_an_unclear_reply_makes_the_agent_ask_again(self, env: ScenarioEnv) -> None:
        env.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END, approved=False))
        env.run()
        review_subject = next(s for s in env.sent_subjects() if s.startswith("Needs your review"))

        env.reply_from_kevin(review_subject, "hmm, not sure")  # unscripted -> unclear
        env.run()

        assert any("ask again" in email.body.lower() for email in env.sender.sent_emails())
        assert env.the_item().status is ItemStatus.NEEDS_REVIEW

    def test_use_the_new_one_replaces_the_timesheet(self, env: ScenarioEnv) -> None:
        env.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END))
        env.run()
        env.add_email(
            PRIYA,
            subject="Corrected timesheet",
            attachment=("timesheet-v2.pdf", b"PDFDATA2"),
            scripted_reading=reading(AUG_START, AUG_END, total_hundredths=15_000),
        )
        env.run()
        item = env.the_item()
        assert item.status is ItemStatus.NEEDS_REVIEW
        review_subject = [s for s in env.sent_subjects() if s.startswith("Needs your review")][-1]

        env.replies["use the new one"] = ReplyReading(
            answers=[ReplyAnswer(review_code="CORRECTION", kind=ReplyAnswerKind.USE_NEW_ONE)]
        )
        env.reply_from_kevin(review_subject, "use the new one")
        env.run()

        item = env.store.get_item(item.id)
        assert item.status is ItemStatus.READY
        assert item.approved_hours is not None
        assert str(item.approved_hours) == "150.00"  # the corrected hours


class TestMondaySummary:
    def test_sent_on_monday_with_the_tracking_sheet(self, env: ScenarioEnv) -> None:
        env.today = date(2026, 9, 7)  # a Monday
        clean_timesheet(env)
        env.run()

        summaries = [s for s in env.sent_subjects() if s.startswith("Weekly summary")]
        assert len(summaries) == 1
        email = next(
            email
            for email in env.sender.sent_emails()
            if email.subject.startswith("Weekly summary")
        )
        assert "Timesheets received last week" in email.body
        assert email.attachments == () or email.attachments[0].filename == "tracking.xlsx"

    def test_not_sent_twice_on_the_same_monday(self, env: ScenarioEnv) -> None:
        env.today = date(2026, 9, 7)
        clean_timesheet(env)
        env.run()
        env.run()
        assert len([s for s in env.sent_subjects() if s.startswith("Weekly summary")]) == 1

    def test_not_sent_on_other_days(self, env: ScenarioEnv) -> None:
        env.today = date(2026, 9, 8)  # a Tuesday
        clean_timesheet(env)
        env.run()
        assert not any(s.startswith("Weekly summary") for s in env.sent_subjects())
