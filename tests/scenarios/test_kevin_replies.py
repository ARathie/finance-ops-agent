"""Kevin answers in his own words, and the agent does what he asked (decision 54).

The reader is scripted here, as everywhere in the scenarios: what is under test
is that code checks each request the reading names, does the ones it can, says
plainly which it could not, and never lets a reading alone send anything.
"""

from dataclasses import replace as dc_replace
from datetime import date

from finance_ops_agent.adapters.fakes.reader import FakeReader
from finance_ops_agent.application.context import Mode
from finance_ops_agent.application.run import run_once
from finance_ops_agent.domain.emails import OutgoingEmail
from finance_ops_agent.domain.items import InvoiceRecord
from finance_ops_agent.domain.reading import ReplyAnswer, ReplyAnswerKind, ReplyReading
from finance_ops_agent.domain.statuses import ItemStatus
from tests.scenarios.conftest import PRIYA, ScenarioEnv, engagement_row, reading

AUG_START, AUG_END = date(2026, 8, 1), date(2026, 8, 31)
KEVIN = "kevin@icon-technologies.com"
CLIENT = ("ap@acme.example",)
NUMBER = "083126AC-PS"

REVISED_REPLY = (
    "The old one is a leftover. Please append -revised to the invoice number"
    " so it can go through, and send it back to me as a draft for approval."
)
REVISED_READING = ReplyReading(
    answers=[
        ReplyAnswer(
            review_code="QUICKBOOKS_FAILED",
            kind=ReplyAnswerKind.INVOICE_NUMBER,
            value=f"{NUMBER}-revised",
            quote="append -revised to the invoice number",
        ),
        ReplyAnswer(
            review_code="QUICKBOOKS_FAILED",
            kind=ReplyAnswerKind.SHOW_ME_FIRST,
            quote="send it back to me as a draft for approval",
        ),
    ],
    understood=f"You want the invoice numbered {NUMBER}-revised and sent to you to approve.",
)


def answer(kind: ReplyAnswerKind, quote: str, value: str | None = None) -> ReplyAnswer:
    return ReplyAnswer(review_code="QUICKBOOKS_FAILED", kind=kind, value=value, quote=quote)


def approval_answer(kind: ReplyAnswerKind, quote: str, value: str | None = None) -> ReplyAnswer:
    return ReplyAnswer(review_code="APPROVAL", kind=kind, value=value, quote=quote)


def to_client(env: ScenarioEnv) -> list[OutgoingEmail]:
    return [email for email in env.sender.sent_emails() if email.to == CLIENT]


def last_reply_body(env: ScenarioEnv) -> str:
    bodies = [
        str(record.payload.get("body", ""))
        for record in env.store.outgoing_records()
        if record.kind == "reply_email"
    ]
    assert bodies, "Kevin was told nothing about his reply"
    return bodies[-1]


def number_already_in_quickbooks(env: ScenarioEnv) -> str:
    """Ask first, with a leftover invoice in QuickBooks holding the month's number."""
    env.mode = Mode.ASK_FIRST
    env.accounting.taken_numbers.add(NUMBER)
    env.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END))
    env.run()
    return next(s for s in env.sent_subjects() if s.startswith("Needs your review"))


class TestANumberQuickBooksAlreadyHas:
    def test_kevin_is_told_how_to_settle_it_from_his_inbox(self, env: ScenarioEnv) -> None:
        number_already_in_quickbooks(env)

        [review] = env.store.open_reviews()
        assert review.code == "QUICKBOOKS_FAILED"
        assert f"already has an invoice numbered {NUMBER}" in review.message
        assert '"try again"' in review.message
        assert f"{NUMBER}-revised" in review.message
        assert env.the_item().status is ItemStatus.READY
        assert to_client(env) == []

    def test_a_new_number_in_his_own_words_goes_through_to_his_approval(
        self, env: ScenarioEnv
    ) -> None:
        """What Kevin actually wrote: add -revised, and show it to me first."""
        subject = number_already_in_quickbooks(env)

        env.replies[REVISED_REPLY] = REVISED_READING
        env.reply_from_kevin(subject, REVISED_REPLY)
        env.run()

        item = env.the_item()
        [invoice] = env.store.invoices_for_item(item.id)
        assert invoice.number == f"{NUMBER}-revised"
        assert item.status is ItemStatus.WAITING_FOR_APPROVAL
        assert any(s.startswith("Approve?") for s in env.sent_subjects())
        assert to_client(env) == []
        body = last_reply_body(env)
        assert f"I'll make the invoice as {NUMBER}-revised" in body
        assert "approve before anything goes to the client" in body
        assert env.store.open_reviews() == []

    def test_the_reader_is_told_which_number_he_is_changing(self, env: ScenarioEnv) -> None:
        subject = number_already_in_quickbooks(env)
        env.replies[REVISED_REPLY] = REVISED_READING
        env.reply_from_kevin(subject, REVISED_REPLY)

        reader = FakeReader(env.readings, env.replies)
        run_once(dc_replace(env.deps(), reader=reader))

        assert reader.last_reply_context is not None
        assert reader.last_reply_context.invoice_number == NUMBER
        assert reader.last_reply_context.consultant == "Priya Shah"

    def test_try_again_after_he_deleted_the_leftover(self, env: ScenarioEnv) -> None:
        subject = number_already_in_quickbooks(env)

        env.accounting.taken_numbers.clear()  # Kevin deleted it in QuickBooks
        env.replies["I deleted it, try again"] = ReplyReading(
            answers=[answer(ReplyAnswerKind.TRY_AGAIN, "try again")]
        )
        env.reply_from_kevin(subject, "I deleted it, try again")
        env.run()

        [invoice] = env.store.invoices_for_item(env.the_item().id)
        assert invoice.number == NUMBER
        assert env.the_item().status is ItemStatus.WAITING_FOR_APPROVAL
        assert "trying again now" in last_reply_body(env)

    def test_try_again_that_fails_again_still_reaches_kevin(self, env: ScenarioEnv) -> None:
        """Kevin said try again but the leftover is still there: he answered the
        first review, so the second failure is a new one, and it is emailed."""
        subject = number_already_in_quickbooks(env)
        env.replies["try again"] = ReplyReading(
            answers=[answer(ReplyAnswerKind.TRY_AGAIN, "try again")]
        )
        env.reply_from_kevin(subject, "try again")
        env.run()

        reviews = [s for s in env.sent_subjects() if s.startswith("Needs your review")]
        assert len(reviews) == 2
        assert [r.code for r in env.store.open_reviews()] == ["QUICKBOOKS_FAILED"]

    def test_a_number_one_of_his_invoices_already_has_is_refused(self, env: ScenarioEnv) -> None:
        subject = number_already_in_quickbooks(env)
        env.store.record_invoice(
            dc_replace(_any_invoice_record(env), number="083126AC-OTHER", item_id=999)
        )
        env.replies["use 083126AC-OTHER"] = ReplyReading(
            answers=[answer(ReplyAnswerKind.INVOICE_NUMBER, "use 083126AC-OTHER", "083126AC-OTHER")]
        )
        env.reply_from_kevin(subject, "use 083126AC-OTHER")
        env.run()

        assert env.store.invoices_for_item(env.the_item().id) == []
        assert [r.code for r in env.store.open_reviews()] == ["QUICKBOOKS_FAILED"]
        body = last_reply_body(env)
        assert "I can't use invoice number 083126AC-OTHER" in body
        assert "already has 083126AC-OTHER" in body

    def test_a_number_quickbooks_cannot_hold_is_refused(self, env: ScenarioEnv) -> None:
        subject = number_already_in_quickbooks(env)
        env.replies["use 083126AC PS revised"] = ReplyReading(
            answers=[
                answer(
                    ReplyAnswerKind.INVOICE_NUMBER,
                    "use 083126AC PS revised",
                    "083126AC PS revised",
                )
            ]
        )
        env.reply_from_kevin(subject, "use 083126AC PS revised")
        env.run()

        assert env.store.invoices_for_item(env.the_item().id) == []
        assert "can't hold" in last_reply_body(env)

    def test_a_request_resting_on_words_he_did_not_write_is_not_done(
        self, env: ScenarioEnv
    ) -> None:
        """The model's say-so is not enough to change an invoice: its quote
        has to be in the reply."""
        subject = number_already_in_quickbooks(env)
        env.replies["thanks"] = ReplyReading(
            answers=[answer(ReplyAnswerKind.INVOICE_NUMBER, "use 083126AC-PS-X", "083126AC-PS-X")]
        )
        env.reply_from_kevin(subject, "thanks")
        env.run()

        assert env.store.invoices_for_item(env.the_item().id) == []
        assert "wasn't sure enough" in last_reply_body(env)
        assert [r.code for r in env.store.open_reviews()] == ["QUICKBOOKS_FAILED"]

    def test_show_me_first_holds_back_an_automatic_invoice(self, env: ScenarioEnv) -> None:
        env.mode = Mode.AUTO
        env.workbook.engagements[0] = engagement_row(2, **{"Send automatically": "yes"})
        env.accounting.taken_numbers.add(NUMBER)
        env.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END))
        env.run()
        subject = next(s for s in env.sent_subjects() if s.startswith("Needs your review"))

        env.replies[REVISED_REPLY] = REVISED_READING
        env.reply_from_kevin(subject, REVISED_REPLY)
        env.run()

        assert env.the_item().status is ItemStatus.WAITING_FOR_APPROVAL
        assert to_client(env) == []
        assert any(s.startswith("Approve?") for s in env.sent_subjects())


def _any_invoice_record(env: ScenarioEnv) -> InvoiceRecord:
    return InvoiceRecord(
        id=0,
        item_id=0,
        number="",
        external_id="ext-other",
        amount_cents=100,
        issue_date=date(2026, 9, 1),
        due_date=date(2026, 10, 1),
        pdf_sha256="",
        status="created",
        replaces_number=None,
    )


class TestApprovalInPlainWords:
    def asked(self, env: ScenarioEnv) -> str:
        env.mode = Mode.ASK_FIRST
        env.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END))
        env.run()
        return next(s for s in env.sent_subjects() if s.startswith("Approve?"))

    def test_looks_good_send_it_sends_it(self, env: ScenarioEnv) -> None:
        approval = self.asked(env)
        env.replies["Looks good, send it."] = ReplyReading(
            answers=[approval_answer(ReplyAnswerKind.APPROVE, "Looks good, send it")]
        )
        env.reply_from_kevin(approval, "Looks good, send it.")
        env.run()

        assert env.the_item().status is ItemStatus.INVOICE_SENT
        [billing] = to_client(env)
        assert KEVIN in billing.cc
        assert "Sent invoice" in last_reply_body(env)

    def test_an_approval_with_a_change_is_not_an_approval(self, env: ScenarioEnv) -> None:
        approval = self.asked(env)
        text = "Fine, but make it 150 hours"
        env.replies[text] = ReplyReading(
            answers=[
                approval_answer(ReplyAnswerKind.APPROVE, "Fine"),
                approval_answer(ReplyAnswerKind.HOURS, "make it 150 hours", "150"),
            ]
        )
        env.reply_from_kevin(approval, text)
        env.run()

        assert env.the_item().status is ItemStatus.WAITING_FOR_APPROVAL
        assert to_client(env) == []
        body = last_reply_body(env)
        assert "already exists in QuickBooks" in body
        assert "cancel" in body

    def test_an_approval_he_did_not_write_sends_nothing(self, env: ScenarioEnv) -> None:
        approval = self.asked(env)
        env.replies["hmm, let me check"] = ReplyReading(
            answers=[approval_answer(ReplyAnswerKind.APPROVE, "yes send it")]
        )
        env.reply_from_kevin(approval, "hmm, let me check")
        env.run()

        assert env.the_item().status is ItemStatus.WAITING_FOR_APPROVAL
        assert to_client(env) == []

    def test_dont_send_this_cancels(self, env: ScenarioEnv) -> None:
        approval = self.asked(env)
        env.replies["Don't send this one, she was out."] = ReplyReading(
            answers=[approval_answer(ReplyAnswerKind.CANCEL, "Don't send this one")]
        )
        env.reply_from_kevin(approval, "Don't send this one, she was out.")
        env.run()

        assert env.the_item().status is ItemStatus.CANCELLED
        assert to_client(env) == []
        assert "voided invoice" in last_reply_body(env)

    def test_a_reply_to_the_agents_follow_up_is_still_about_the_approval(
        self, env: ScenarioEnv
    ) -> None:
        """Kevin answers the "I couldn't tell" email, not the original one; that
        answer still approves the invoice it was about."""
        approval = self.asked(env)
        env.reply_from_kevin(approval, "what is this?")  # unscripted: unclear
        env.run()
        follow_up = next(
            record.payload["subject"]
            for record in env.store.outgoing_records()
            if record.kind == "reply_email"
        )

        env.reply_from_kevin(str(follow_up), "approve")
        env.run()

        assert env.the_item().status is ItemStatus.INVOICE_SENT
        assert len(to_client(env)) == 1


def test_a_quote_must_be_his_whole_words(env: ScenarioEnv) -> None:
    """A model quoting "A" must be pointing at Kevin's "A", not the a in "thanks"."""
    subject = number_already_in_quickbooks(env)
    env.replies["thanks"] = ReplyReading(answers=[answer(ReplyAnswerKind.TRY_AGAIN, "a")])
    env.reply_from_kevin(subject, "thanks")
    env.run()

    assert "wasn't sure enough" in last_reply_body(env)


def test_try_again_on_a_question_about_a_timesheet_is_not_taken(env: ScenarioEnv) -> None:
    """It would close the question with nothing answered."""
    env.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END, approved=False))
    env.run()
    subject = next(s for s in env.sent_subjects() if s.startswith("Needs your review"))
    env.replies["try again"] = ReplyReading(
        answers=[
            ReplyAnswer(
                review_code="NO_APPROVAL", kind=ReplyAnswerKind.TRY_AGAIN, quote="try again"
            )
        ]
    )
    env.reply_from_kevin(subject, "try again")
    env.run()

    assert [r.code for r in env.store.open_reviews()] == ["NO_APPROVAL"]
    assert env.the_item().status is ItemStatus.NEEDS_REVIEW
    assert "nothing for me to try again" in last_reply_body(env)
