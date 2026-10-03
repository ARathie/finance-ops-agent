"""A client with no billing email, through whole runs (decision 65, gap G6).

The client is still a client: its timesheets are read and placed. Only the
invoice waits, with a NO_BILLING_CONTACT question that says where to add the
address, and the item carries on by itself on the first run that finds one.
"""

from datetime import date

from finance_ops_agent.domain.reading import ReplyAnswer, ReplyAnswerKind, ReplyReading
from finance_ops_agent.domain.statuses import ItemStatus
from finance_ops_agent.ports.accounting import AccountingParty
from tests.scenarios.conftest import PRIYA, ScenarioEnv, client_row, reading

AUG_START, AUG_END = date(2026, 8, 1), date(2026, 8, 31)


def open_codes(env: ScenarioEnv) -> set[str]:
    return {review.code for review in env.store.open_reviews()}


def timesheet_for_a_client_with_no_billing_email(env: ScenarioEnv) -> None:
    env.workbook.clients[0] = client_row(2, **{"Billing email": ""})
    env.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END))
    env.run()


class TestTheInvoiceWaits:
    def test_the_timesheet_is_placed_and_the_invoice_waits(self, env: ScenarioEnv) -> None:
        timesheet_for_a_client_with_no_billing_email(env)

        item = env.the_item()
        assert (item.consultant, item.client) == ("Priya Shah", "Acme Corp")
        assert item.status is ItemStatus.NEEDS_REVIEW
        assert "NO_BILLING_CONTACT" in open_codes(env)
        # Before decision 65 the client was dropped, so the timesheet looked
        # like one for a client nobody knew.
        assert "ENGAGEMENT_UNCLEAR" not in open_codes(env)
        assert env.store.invoices_for_item(item.id) == []

    def test_kevin_is_told_where_to_add_it(self, env: ScenarioEnv) -> None:
        timesheet_for_a_client_with_no_billing_email(env)

        [review] = [
            email
            for email in env.sender.sent_emails()
            if email.subject.startswith("Needs your review: Priya Shah")
        ]
        assert "There is no billing email for Acme Corp" in review.body
        assert "Acme Corp's row on the Clients sheet" in review.body
        [about_the_list] = [
            email
            for email in env.sender.sent_emails()
            if email.subject.startswith("Needs your review: the engagement list")
        ]
        assert "Clients sheet, row 2: there is no billing email" in about_the_list.body

    def test_it_goes_ahead_by_itself_once_the_address_is_added(self, env: ScenarioEnv) -> None:
        timesheet_for_a_client_with_no_billing_email(env)
        item = env.the_item()

        env.workbook.clients[0] = client_row()
        env.run()

        item = env.store.get_item(item.id)
        assert item.status is ItemStatus.READY
        assert item.snapshot.billing_emails == ["ap@acme.example"]
        assert open_codes(env) == set()

    def test_it_keeps_waiting_while_there_is_still_none(self, env: ScenarioEnv) -> None:
        timesheet_for_a_client_with_no_billing_email(env)
        asked = len(env.sender.sent_emails())

        env.run()

        assert env.the_item().status is ItemStatus.NEEDS_REVIEW
        assert "NO_BILLING_CONTACT" in open_codes(env)
        assert len(env.sender.sent_emails()) == asked  # asked once, not every run

    def test_ignore_drops_the_timesheet(self, env: ScenarioEnv) -> None:
        timesheet_for_a_client_with_no_billing_email(env)
        subject = next(
            s for s in env.sent_subjects() if s.startswith("Needs your review: Priya Shah")
        )

        env.replies["ignore"] = ReplyReading(
            answers=[ReplyAnswer(review_code="NO_BILLING_CONTACT", kind=ReplyAnswerKind.IGNORE)]
        )
        env.reply_from_kevin(subject, "ignore")
        env.run()

        assert env.the_item().status is ItemStatus.IGNORED


class TestNoQuestion:
    def test_a_portal_client_needs_no_billing_email(self, env: ScenarioEnv) -> None:
        env.workbook.clients[0] = client_row(2, Delivery="portal", **{"Billing email": ""})
        env.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END))
        env.run()

        assert env.the_item().status is ItemStatus.READY
        assert "NO_BILLING_CONTACT" not in open_codes(env)

    def test_quickbooks_has_the_address_the_list_lacks(self, env: ScenarioEnv) -> None:
        """QuickBooks' customer email is used where it has one (decision 52), so
        there is somewhere to send the invoice after all."""
        env.workbook.clients[0] = client_row(2, **{"Billing email": ""})
        env.accounting.customers["Acme Corporation"] = AccountingParty(
            ref="58",
            name="Acme Corporation",
            company="Acme Corporation",
            email="ap@acme.example",
            payment_terms_days=30,
        )
        env.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END))
        env.run()

        item = env.the_item()
        assert item.snapshot.billing_emails == ["ap@acme.example"]
        assert "NO_BILLING_CONTACT" not in open_codes(env)
