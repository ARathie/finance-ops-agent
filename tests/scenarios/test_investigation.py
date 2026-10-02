"""The agent looks into what is stuck, tells Kevin what it found and what he could
do, and acts on the option he picks (decision 56).

Stage 1 is plain code: the run calls the diagnosis where it gets stuck. Stage 3
is the investigator, scripted here: it makes real calls through the real
read-only toolbox and gives a scripted answer, so what is under test is
everything around the model -- what it is handed, what reaches Kevin, and what
his reply does.
"""

from datetime import date

from finance_ops_agent.adapters.fakes.investigator import FakeInvestigator
from finance_ops_agent.application.context import Mode
from finance_ops_agent.application.investigation import MAX_PER_RUN
from finance_ops_agent.domain.investigation import Investigation, Proposal
from finance_ops_agent.domain.items import InvoiceRecord
from finance_ops_agent.domain.statuses import ItemStatus
from finance_ops_agent.ports.accounting import InvoiceLookup
from tests.scenarios.conftest import PRIYA, ScenarioEnv, reading

AUG_START, AUG_END = date(2026, 8, 1), date(2026, 8, 31)
NUMBER = "083126AC-PS"
CLIENT = ("ap@acme.example",)


def review_bodies(env: ScenarioEnv) -> list[str]:
    return [
        str(record.payload.get("body", ""))
        for record in env.store.outgoing_records()
        if record.kind == "review_email"
    ]


def held_by_forgotten_item(env: ScenarioEnv) -> None:
    """Manoj's case: the month's number is held by the agent's own invoice for
    item 30, which `fops forget` removed."""
    env.mode = Mode.ASK_FIRST
    env.accounting.taken_numbers.add(NUMBER)
    env.accounting.other_invoices["2"] = InvoiceLookup(
        external_id="2",
        number=NUMBER,
        total_cents=2_184_000,
        balance_cents=2_184_000,
        customer="Acme Corporation",
        item_id=30,
        issued="2026-09-01",
    )


FOUND = Investigation(
    found=(
        f"QuickBooks already has invoice {NUMBER}. It is the agent's own invoice for an"
        " item that was removed during testing, so it is a leftover."
    ),
    evidence=[f"invoice {NUMBER} carries the note for item 30", "item 30 is not in the records"],
    proposals=[
        Proposal(
            what_to_do=f"Delete invoice {NUMBER} in QuickBooks (delete, not void).",
            reply_to_choose="try again",
            why="If the old invoice was only a test.",
        ),
        Proposal(
            what_to_do="Make this invoice under a new number.",
            reply_to_choose=f"use {NUMBER}-revised",
            why="If the old invoice is real and must stay.",
        ),
        Proposal(what_to_do="Send it to the client now.", reply_to_choose="approve"),
    ],
    sure=True,
)


def investigator() -> FakeInvestigator:
    return FakeInvestigator(
        answer=FOUND,
        calls_to_make=[
            ("describe_item", {"item_id": 1}),
            ("explain_invoice_number", {"number": NUMBER, "item_id": 1}),
        ],
    )


# --- stage 1: the run calls the diagnosis itself ---


class TestTheRunLooksForItself:
    def test_a_taken_number_says_whose_invoice_holds_it(self, env: ScenarioEnv) -> None:
        held_by_forgotten_item(env)
        env.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END))
        env.run()

        [review] = env.store.open_reviews()
        assert "What I found:" in review.message
        assert "item 30" in review.message
        assert "no longer in my" in review.message

    def test_an_invoice_quickbooks_lacks_gets_its_own_review_and_the_rest_are_checked(
        self, env: ScenarioEnv
    ) -> None:
        """Invoice 153: before, it failed the paid check for every invoice, every run."""
        env.mode = Mode.ASK_FIRST
        env.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END))
        env.run()
        approval = next(s for s in env.sent_subjects() if s.startswith("Approve?"))
        env.reply_from_kevin(approval, "approve")
        env.run()
        item = env.the_item()
        [real] = env.store.invoices_for_item(item.id)
        env.store.record_invoice(
            InvoiceRecord(
                id=0,
                item_id=item.id,
                number="053125IS-SA",
                external_id="153",
                amount_cents=100,
                issue_date=date(2025, 6, 1),
                due_date=date(2025, 7, 1),
                pdf_sha256="",
                status="sent",
                replaces_number=None,
            )
        )
        env.accounting.paid.add(real.external_id)
        env.store.set_state("last_paid_check", "")

        env.run()

        assert env.the_item().status is ItemStatus.CLIENT_PAID  # the real one was still checked
        missing = [r for r in env.store.open_reviews() if "id 153" in r.message]
        assert len(missing) == 1
        assert "fops forget" in missing[0].message
        assert any("id 153" in body for body in review_bodies(env))  # and Kevin is emailed
        assert env.store.get_state("last_paid_check") == env.today.isoformat()

    def test_the_monday_summary_lists_what_looks_stuck(self, env: ScenarioEnv) -> None:
        held_by_forgotten_item(env)
        env.today = date(2026, 9, 14)  # a Monday
        env.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END))
        env.run()

        summary = next(
            record.payload["body"]
            for record in env.store.outgoing_records()
            if record.kind == "summary_email"
        )
        assert "Things that look stuck" in str(summary)
        assert "item 30" in str(summary)


# --- stage 3: the investigator ---


class TestTheInvestigator:
    def test_kevin_is_told_what_was_found_and_what_he_could_do(self, env: ScenarioEnv) -> None:
        held_by_forgotten_item(env)
        env.investigator = investigator()
        env.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END))
        env.run()

        [body] = review_bodies(env)
        assert "What I found (I looked into this before writing):" in body
        assert "leftover" in body
        assert (
            'A. Delete invoice 083126AC-PS in QuickBooks (delete, not void). Reply "try again"'
            in body
        )
        assert f'B. Make this invoice under a new number. Reply "use {NUMBER}-revised"' in body
        assert "C." not in body  # "approve" is never offered on a review email
        sent = next(
            e for e in env.sender.sent_emails() if e.subject.startswith("Needs your review")
        )
        assert "What you could do:" in sent.body  # what was sent is what was written down

    def test_it_looked_through_the_real_tools(self, env: ScenarioEnv) -> None:
        held_by_forgotten_item(env)
        env.investigator = investigator()
        env.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END))
        env.run()

        assert env.investigator is not None
        fake = env.investigator
        assert isinstance(fake, FakeInvestigator)
        [problem] = fake.problems
        assert "already has an invoice numbered" in problem
        assert "Priya Shah at Acme Corp" in problem
        described, explained = fake.answers_seen
        assert "QUICKBOOKS_FAILED" in described
        assert "number_held_by_forgotten_item" in explained
        record = next(r for r in env.store.outgoing_records() if r.kind == "review_email")
        calls = record.payload["investigation"]["tool_calls"]  # type: ignore[index]
        assert [call["name"] for call in calls] == ["describe_item", "explain_invoice_number"]

    def test_no_answer_leaves_the_email_as_it_was(self, env: ScenarioEnv) -> None:
        held_by_forgotten_item(env)
        env.investigator = FakeInvestigator(answer=None)
        env.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END))
        env.run()

        [body] = review_bodies(env)
        assert "What I found (I looked" not in body
        assert "already has an invoice numbered" in body

    def test_picking_a_lets_him_answer_with_one_letter(self, env: ScenarioEnv) -> None:
        held_by_forgotten_item(env)
        env.investigator = investigator()
        env.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END))
        env.run()
        subject = next(s for s in env.sent_subjects() if s.startswith("Needs your review"))

        env.accounting.taken_numbers.clear()  # he deleted the leftover, as option A said
        env.accounting.other_invoices.clear()
        from finance_ops_agent.domain.reading import ReplyAnswer, ReplyAnswerKind, ReplyReading

        env.replies["try again"] = ReplyReading(
            answers=[
                ReplyAnswer(
                    review_code="QUICKBOOKS_FAILED",
                    kind=ReplyAnswerKind.TRY_AGAIN,
                    quote="try again",
                )
            ]
        )
        env.reply_from_kevin(subject, "A")
        env.run()

        [invoice] = env.store.invoices_for_item(env.the_item().id)
        assert invoice.number == NUMBER
        assert env.the_item().status is ItemStatus.WAITING_FOR_APPROVAL
        assert not any(email.to == CLIENT for email in env.sender.sent_emails())

    def test_picking_b_uses_the_new_number(self, env: ScenarioEnv) -> None:
        held_by_forgotten_item(env)
        env.investigator = investigator()
        env.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END))
        env.run()
        subject = next(s for s in env.sent_subjects() if s.startswith("Needs your review"))
        from finance_ops_agent.domain.reading import ReplyAnswer, ReplyAnswerKind, ReplyReading

        words = f"use {NUMBER}-revised"
        env.replies[words] = ReplyReading(
            answers=[
                ReplyAnswer(
                    review_code="QUICKBOOKS_FAILED",
                    kind=ReplyAnswerKind.INVOICE_NUMBER,
                    value=f"{NUMBER}-revised",
                    quote=words,
                )
            ]
        )
        env.reply_from_kevin(subject, "Option B please")
        env.run()

        [invoice] = env.store.invoices_for_item(env.the_item().id)
        assert invoice.number == f"{NUMBER}-revised"

    def test_an_email_already_on_its_way_is_never_changed(self, env: ScenarioEnv) -> None:
        held_by_forgotten_item(env)
        env.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END))
        env.run()  # no investigator: the review email goes out as it is
        env.investigator = investigator()

        env.run()

        assert env.investigator is not None
        assert isinstance(env.investigator, FakeInvestigator)
        assert env.investigator.problems == []  # nothing pending to look into

    def test_a_run_looks_into_only_so_many(self, env: ScenarioEnv) -> None:
        from finance_ops_agent.application.context import RunReport
        from finance_ops_agent.application.investigation import investigate_pending_reviews
        from finance_ops_agent.domain import emails

        env.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END))
        env.run()
        item = env.the_item()
        for index in range(MAX_PER_RUN + 2):
            env.store.open_review(item.id, "QUICKBOOKS_FAILED", f"problem {index}")
            email = emails.needs_review("kevin@icon-technologies.com", "x", [f"problem {index}"])
            env.store.record_outgoing(
                "review_email", f"review:test:{index}", item.id, email.payload()
            )
        fake = FakeInvestigator(answer=FOUND)
        env.investigator = fake

        report = RunReport()
        investigate_pending_reviews(env.deps(), report, None)

        assert len(fake.problems) == MAX_PER_RUN
        assert any("rest go out as they are" in note for note in report.lines)


def test_the_problem_it_is_given_carries_no_money(env: ScenarioEnv) -> None:
    held_by_forgotten_item(env)
    fake = investigator()
    env.investigator = fake
    env.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END))
    env.run()

    [problem] = fake.problems
    assert "$[amount]" in problem  # the holder's total was in the review text
    assert "21,840" not in problem
    assert all("21,840" not in seen for seen in fake.answers_seen)
