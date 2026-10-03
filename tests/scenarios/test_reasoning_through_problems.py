"""The agent reasons through what is stuck, wherever it is stuck (decision 67).

Not new flows for each kind of trouble: the investigator looks into every
review email -- an email it could not place or read, the engagement list's
own problems, as well as a timesheet item -- with tools that can see the
email, what was read off the timesheet and the engagement list. Code tells it
which replies each email understands and drops anything else. And the dead
ends the failure-mode list turned up are closed: a timesheet that could not
become an item can be handled again, the reader's reason for failing reaches
Kevin, and an invoice voided by hand is never taken for paid.
"""

from datetime import date

from finance_ops_agent.adapters.fakes.investigator import FakeInvestigator
from finance_ops_agent.application.context import Mode
from finance_ops_agent.application.diagnosis import FindingKind, check_recorded_invoices
from finance_ops_agent.application.paid_check import check_paid_invoices
from finance_ops_agent.domain.investigation import Investigation, Proposal
from finance_ops_agent.domain.periods import BillingPeriod
from finance_ops_agent.domain.reading import ReplyAnswer, ReplyAnswerKind, ReplyReading
from finance_ops_agent.domain.statuses import ItemStatus
from tests.scenarios.conftest import (
    PRIYA,
    ScenarioEnv,
    client_row,
    engagement_row,
    reading,
)

AUG_START, AUG_END = date(2026, 8, 1), date(2026, 8, 31)
HOME = "priya.shah.home@gmail.example"


def review_records(env: ScenarioEnv) -> list[dict[str, object]]:
    return [
        record.payload for record in env.store.outgoing_records() if record.kind == "review_email"
    ]


def looked_into(
    env: ScenarioEnv, *proposals: Proposal, calls: list[tuple[str, dict[str, object]]] | None = None
) -> FakeInvestigator:
    investigator = FakeInvestigator(
        answer=Investigation(found="What I found.", proposals=list(proposals), sure=True),
        calls_to_make=calls or [],
    )
    env.investigator = investigator
    return investigator


def option(reply: str, what: str = "Do this.") -> Proposal:
    return Proposal(what_to_do=what, reply_to_choose=reply, why="When it fits.")


class TestAnEmailWithNoItemIsLookedInto:
    def test_a_timesheet_from_an_address_nobody_knows(self, env: ScenarioEnv) -> None:
        investigator = looked_into(
            env,
            option("this is from Priya Shah"),
            option("this is from Manoj Koottappilly"),  # nobody the list has
            option("approved by <name> on <date>"),  # a blank, not a reply
            option("use 156 hours"),  # not something this email understands
            option("", "Ask Priya which address she will use from now on."),
            calls=[
                ("describe_email", {"message_id": "<01-email@example>"}),
                ("look_up_engagements", {"name_or_address": "Priya Shah"}),
            ],
        )
        env.add_email(HOME, scripted_reading=reading(AUG_START, AUG_END))
        env.run()

        [problem] = investigator.problems
        assert "message id <01-email@example>" in problem
        assert "- this is from <consultant>" in problem and "- try again" in problem
        assert "use <N> hours" not in problem
        email_seen, list_seen = investigator.answers_seen
        assert HOME in email_seen
        assert PRIYA in list_seen  # the address the list has for her

        [payload] = review_records(env)
        investigation = payload["investigation"]
        assert isinstance(investigation, dict)
        offered = [p["reply_to_choose"] for p in investigation["proposals"]]
        assert offered == ["this is from Priya Shah", ""]
        assert "What I found" in str(payload["body"])

    def test_the_letter_picks_the_name_offered(self, env: ScenarioEnv) -> None:
        looked_into(env, option("this is from Priya Shah"))
        env.add_email(HOME, scripted_reading=reading(AUG_START, AUG_END))
        env.run()
        subject = next(s for s in env.sent_subjects() if s.startswith("Needs your review"))

        env.replies["this is from Priya Shah"] = ReplyReading(
            answers=[
                ReplyAnswer(
                    review_code="UNKNOWN_SENDER",
                    kind=ReplyAnswerKind.CONSULTANT_NAME,
                    value="Priya Shah",
                    quote="this is from Priya Shah",
                )
            ]
        )
        env.reply_from_kevin(subject, "A")
        env.run()

        item = env.the_item()
        assert (item.consultant, item.status) == ("Priya Shah", ItemStatus.READY)

    def test_the_engagement_lists_problems(self, env: ScenarioEnv) -> None:
        investigator = looked_into(
            env,
            option("try again"),  # nothing Kevin replies fixes a row
            option("", 'Rename "Acme Corp." on the Engagements sheet to "Acme Corp".'),
            calls=[("look_up_engagements", {"name_or_address": "Acme Corp."})],
        )
        env.workbook.engagements[0] = engagement_row(Client="Acme Corp.")
        env.run()

        [problem] = investigator.problems
        assert "engagement list itself" in problem
        assert "Nothing Kevin replies to this email changes anything" in problem
        assert "Acme Corp" in investigator.answers_seen[0]
        [payload] = [p for p in review_records(env) if p.get("list_problems")]
        investigation = payload["investigation"]
        assert isinstance(investigation, dict)
        assert [p["reply_to_choose"] for p in investigation["proposals"]] == [""]

    def test_an_email_with_no_attachment_takes_only_ignore(self, env: ScenarioEnv) -> None:
        investigator = looked_into(env, option("ignore"), option("use 156 hours"))
        env.add_email(PRIYA, subject="My timesheet", attachment=None)
        env.run()

        [problem] = investigator.problems
        assert "- ignore" in problem and "use <N> hours" not in problem
        [payload] = review_records(env)
        investigation = payload["investigation"]
        assert isinstance(investigation, dict)
        assert [p["reply_to_choose"] for p in investigation["proposals"]] == ["ignore"]

    def test_a_timesheet_question_offers_no_correction_where_none_came(
        self, env: ScenarioEnv
    ) -> None:
        """The first live run offered "use the new one" on a timesheet with no
        corrected version behind it (decision 67)."""
        investigator = looked_into(
            env, option("use the new one"), option("approved by Jane Doe on 8/31")
        )
        env.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END, approved=False))
        env.run()

        replies = investigator.problems[0].split("The replies the agent understands")[1]
        assert "use the new one" not in replies.split("The email as written")[0]
        [payload] = review_records(env)
        investigation = payload["investigation"]
        assert isinstance(investigation, dict)
        assert [p["reply_to_choose"] for p in investigation["proposals"]] == [
            "approved by Jane Doe on 8/31"
        ]


class TestATimesheetThatCouldNotBecomeAnItem:
    """Dates outside the engagement, a rate that is not there yet: fixed in the
    list. Before decision 67 the email was a dead end -- nothing read it again,
    and the same file sent again was filed as a duplicate."""

    def no_rate_for_august(self, env: ScenarioEnv) -> str:
        env.workbook.engagements[0] = engagement_row(2, **{"Rates from": "2026-09-01"})
        env.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END))
        env.run()
        assert env.store.find_item("Priya Shah", "Acme Corp", _august()) is None
        return next(s for s in env.sent_subjects() if s.startswith("Needs your review"))

    def test_it_is_set_aside_with_try_again(self, env: ScenarioEnv) -> None:
        subject = self.no_rate_for_august(env)
        [email] = [e for e in env.sender.sent_emails() if e.subject == subject]
        assert 'reply "try again"' in email.body
        assert "this is from" not in email.body  # the consultant is not the question
        assert "fill in the form" not in email.body  # nothing new to set up

    def test_try_again_after_fixing_the_list_handles_it(self, env: ScenarioEnv) -> None:
        subject = self.no_rate_for_august(env)
        env.workbook.engagements[0] = engagement_row()  # rates from August now
        env.replies["fixed it, try again"] = ReplyReading(
            answers=[
                ReplyAnswer(
                    review_code="RATE_MISSING",
                    kind=ReplyAnswerKind.TRY_AGAIN,
                    quote="try again",
                )
            ]
        )
        env.reply_from_kevin(subject, "fixed it, try again")
        env.run()
        env.run()

        item = env.store.find_item("Priya Shah", "Acme Corp", _august())
        assert item is not None
        assert item.status is ItemStatus.READY


def _august() -> BillingPeriod:
    return BillingPeriod(AUG_START, AUG_END)


class TestWhyAFileCouldNotBeRead:
    def test_the_readers_reason_reaches_kevin(self, env: ScenarioEnv) -> None:
        env.add_email(PRIYA, attachment=("locked.pdf", b"%PDF-encrypted"))  # no reading
        env.run()

        [review] = [r for r in env.store.open_reviews() if r.code == "CANT_READ_ATTACHMENT"]
        assert "What stopped me: no scripted reading for locked.pdf" in review.message


class TestAnInvoiceVoidedByHand:
    def sent(self, env: ScenarioEnv) -> tuple[int, str]:
        env.mode = Mode.AUTO
        env.workbook.engagements[0] = engagement_row(2, **{"Send automatically": "yes"})
        env.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END))
        env.run()
        item = env.the_item()
        assert item.status is ItemStatus.INVOICE_SENT
        [invoice] = env.store.invoices_for_item(item.id)
        return item.id, invoice.external_id

    def test_it_is_never_taken_for_paid(self, env: ScenarioEnv) -> None:
        item_id, external_id = self.sent(env)
        env.accounting.cancelled.append(external_id)  # voided in QuickBooks: total and balance 0
        env.accounting.paid.add(external_id)

        check_paid_invoices(env.deps(), env.report(), force=True)

        assert env.store.get_item(item_id).status is ItemStatus.INVOICE_SENT

    def test_diagnosis_says_it_was_voided(self, env: ScenarioEnv) -> None:
        from finance_ops_agent.application.diagnosis import looking_at

        item_id, external_id = self.sent(env)
        env.accounting.cancelled.append(external_id)

        findings = check_recorded_invoices(looking_at(env.deps()))

        [finding] = [f for f in findings if f.kind is FindingKind.INVOICE_VOIDED_IN_ACCOUNTING]
        assert finding.item_id == item_id
        assert "voided" in finding.what


def test_a_portal_client_is_unaffected(env: ScenarioEnv) -> None:
    """Guard: nothing here changes a clean timesheet's path."""
    env.workbook.clients[0] = client_row(2, Delivery="portal", **{"Billing email": ""})
    env.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END))
    env.run()
    assert env.the_item().status is ItemStatus.READY
