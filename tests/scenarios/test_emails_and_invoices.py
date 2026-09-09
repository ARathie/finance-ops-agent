"""Whole runs that send: the modes, Kevin's replies, and never-twice sending."""

from dataclasses import replace as dc_replace
from datetime import date

import pytest

from finance_ops_agent.adapters.fakes.sender import FakeSender, SimulatedCrash
from finance_ops_agent.application.context import Mode
from finance_ops_agent.application.run import run_once
from finance_ops_agent.domain.money import Money
from finance_ops_agent.domain.reading import ReplyAnswer, ReplyAnswerKind, ReplyReading
from finance_ops_agent.domain.statuses import ItemStatus
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


class TestNeverTwice:
    def test_a_crash_between_about_to_send_and_sent_results_in_exactly_one_email(
        self, env: ScenarioEnv, tmp_path: object
    ) -> None:
        env.mode = Mode.AUTO
        from tests.scenarios.conftest import engagement_row

        env.workbook.engagements[0] = engagement_row(2, **{"Send automatically": "yes"})
        clean_timesheet(env)

        # The provider accepts every send, then the machine dies before the
        # agent can write down that it was sent. Each run gets one step further,
        # so the crash lands on the billing email too.
        crashing = FakeSender(env.sender.outbox, crash_after_send=True)
        # Run 1 dies on the details email; run 2 recovers it and dies on the
        # billing email, which is the one that must never go out twice.
        for _ in range(2):
            with pytest.raises(SimulatedCrash):
                run_once(dc_replace(env.deps(), sender=crashing))

        billing = next(
            record for record in env.store.outgoing_records() if record.kind == "billing_email"
        )
        assert billing.status == "in_flight", "the billing send crashed mid-flight"
        assert len(crashing.sent) == 2, "the provider really did send it"

        # Restart on the provider's own state, with the machine healthy again.
        recovered = FakeSender(env.sender.outbox)
        recovered.drafts = crashing.drafts
        recovered.sent = crashing.sent
        env.sender = recovered
        env.run()

        assert [record.status for record in env.store.outgoing_records()].count("in_flight") == 0
        billing_sent = [
            email for email in recovered.sent_emails() if email.to == ("ap@acme.example",)
        ]
        assert len(billing_sent) == 1, "the billing email went out exactly once"

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
            def send(self, draft_id: str) -> None:
                raise RuntimeError("the mailbox is not answering")

        broken = BrokenSender(env.sender.outbox)
        for _ in range(3):
            with pytest.raises(RuntimeError):
                run_once(dc_replace(env.deps(), sender=broken))
            # Each failed attempt leaves the record in flight; the next run
            # reconciles it (the draft is still a draft) and tries again.

        codes = {review.code for review in env.store.open_reviews()}
        assert "SEND_FAILED" in codes


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
