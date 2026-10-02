"""Setting up a new consultant, client or engagement in QuickBooks from Kevin's
email (decision 56).

An email or a timesheet the agent cannot place offers the setup as one of the
answers. Kevin fills in the form; code reads it; the agent shows him exactly
what it will make and waits for "confirm" with a one-time code; only then is
QuickBooks touched, and the email that started it is handled.
"""

import re
from datetime import date

import pytest

from finance_ops_agent.application.run import Mode
from finance_ops_agent.domain import setup
from finance_ops_agent.domain.statuses import ItemStatus
from finance_ops_agent.ports.accounting import AccountingFailed
from tests.scenarios.conftest import ScenarioEnv, reading
from tests.scenarios.test_engagement_copy import qbo  # noqa: F401 - a fixture

AUG_START, AUG_END = date(2026, 8, 1), date(2026, 8, 31)
SAM = "sam@example.com"


def form_reply(**fields: str) -> str:
    values = {
        setup.CONSULTANT: "Sam Okafor",
        setup.CONSULTANT_EMAIL: SAM,
        setup.PAY_WITHIN: "15",
        setup.CLIENT: "Acme Corp",
        setup.BILL_RATE: "150.00",
        setup.PAY_RATE: "110.00",
        setup.START: "2026-08-01",
    }
    values.update(fields)
    return "\n".join(f"{label}: {value}" for label, value in values.items())


def latest(env: ScenarioEnv, subject_starts: str) -> tuple[str, str]:
    email = [e for e in env.sender.sent_emails() if e.subject.startswith(subject_starts)][-1]
    return email.subject, email.body


def reply_to(env: ScenarioEnv, subject_starts: str, body: str) -> None:
    subject, _ = latest(env, subject_starts)
    env.reply_from_kevin(subject, body)


def the_code(env: ScenarioEnv) -> str:
    _, body = latest(env, "Set up in QuickBooks?")
    found = re.search(r'"confirm (\d{4})"', body)
    assert found is not None, body
    return found.group(1)


@pytest.fixture
def stranger(qbo: ScenarioEnv) -> ScenarioEnv:  # noqa: F811
    """Sam, not in QuickBooks, sends his August timesheet."""
    qbo.mode = Mode.ASK_FIRST
    qbo.add_email(SAM, scripted_reading=reading(AUG_START, AUG_END, consultant="Sam Okafor"))
    qbo.run()
    return qbo


@pytest.fixture
def filled_in(stranger: ScenarioEnv) -> ScenarioEnv:
    reply_to(stranger, "Needs your review", form_reply())
    stranger.run()
    return stranger


class TestTheOffer:
    def test_the_question_offers_a_setup_with_what_is_known(self, stranger: ScenarioEnv) -> None:
        _, body = latest(stranger, "Needs your review")
        assert "new consultant, client or engagement" in body
        assert f"Consultant email: {SAM}" in body
        assert "Bill rate:" in body and "Pay rate:" in body

    def test_not_offered_while_the_engagements_come_from_the_spreadsheet(
        self, env: ScenarioEnv
    ) -> None:
        env.add_email(SAM, scripted_reading=reading(AUG_START, AUG_END))
        env.run()
        _, body = latest(env, "Needs your review")
        assert "Bill rate:" not in body


class TestKevinFillsItIn:
    def test_he_is_shown_exactly_what_will_be_made_and_nothing_is_yet(
        self, filled_in: ScenarioEnv
    ) -> None:
        subject, body = latest(filled_in, "Set up in QuickBooks?")
        assert subject == "Set up in QuickBooks? Sam Okafor at Acme Corp"
        assert "Bill rate $150.00 an hour" in body
        assert "Pay rate $110.00 an hour" in body
        assert "Existing client Acme Corp." in body
        assert filled_in.accounting.set_up == []

    def test_the_form_is_read_by_code_not_the_model(self, filled_in: ScenarioEnv) -> None:
        """The fake reader answers "unclear" to anything not scripted; a
        confirmation going out means the model was never asked."""
        assert not any("couldn't tell" in e.body for e in filled_in.sender.sent_emails())

    def test_problems_are_named_and_his_answers_shown_back(self, stranger: ScenarioEnv) -> None:
        reply_to(stranger, "Needs your review", form_reply(**{setup.BILL_RATE: "about 150"}))
        stranger.run()

        email = stranger.sender.sent_emails()[-1]
        assert "I couldn't set this up yet" in email.body
        assert '"Bill rate" should be an amount an hour' in email.body
        assert "Bill rate: about 150" in email.body
        assert stranger.accounting.set_up == []


class TestKevinConfirms:
    def test_confirm_with_the_code_sets_it_up_and_handles_the_timesheet(
        self, filled_in: ScenarioEnv
    ) -> None:
        reply_to(filled_in, "Set up in QuickBooks?", f"confirm {the_code(filled_in)}")
        filled_in.run()

        (made,) = filled_in.accounting.set_up
        assert made.consultant == "Sam Okafor"
        subject, body = latest(filled_in, "Set up in QuickBooks:")
        assert subject == "Set up in QuickBooks: Sam Okafor at Acme Corp"
        assert "made vendor Sam Okafor" in body
        assert "used the existing customer Acme Corp" in body
        sam = [i for i in filled_in.store.list_items() if i.consultant == "Sam Okafor"]
        assert len(sam) == 1
        assert sam[0].snapshot.bill_rate_cents == 15_000  # off the product it made
        assert sam[0].status is not ItemStatus.NEEDS_REVIEW
        assert not any(r.code == "UNKNOWN_SENDER" for r in filled_in.store.open_reviews())

    def test_it_is_written_down_before_quickbooks_is_touched(self, filled_in: ScenarioEnv) -> None:
        reply_to(filled_in, "Set up in QuickBooks?", f"confirm {the_code(filled_in)}")
        filled_in.run()

        (record,) = [r for r in filled_in.store.outgoing_records() if r.kind == "quickbooks_setup"]
        assert record.status == "done"
        assert record.payload["bill_rate_cents"] == 15_000

    def test_a_wrong_code_changes_nothing(self, filled_in: ScenarioEnv) -> None:
        code = the_code(filled_in)
        wrong = "1000" if code != "1000" else "1001"
        reply_to(filled_in, "Set up in QuickBooks?", f"confirm {wrong}")
        filled_in.run()

        assert filled_in.accounting.set_up == []
        assert "Nothing has been changed in QuickBooks" in filled_in.sender.sent_emails()[-1].body

    def test_confirm_without_the_code_changes_nothing(self, filled_in: ScenarioEnv) -> None:
        reply_to(filled_in, "Set up in QuickBooks?", "confirm")
        filled_in.run()
        assert filled_in.accounting.set_up == []

    def test_cancel_leaves_quickbooks_alone_and_the_question_open(
        self, filled_in: ScenarioEnv
    ) -> None:
        reply_to(filled_in, "Set up in QuickBooks?", "cancel")
        filled_in.run()

        assert filled_in.accounting.set_up == []
        assert any(r.code == "UNKNOWN_SENDER" for r in filled_in.store.open_reviews())


class TestANewClient:
    def test_the_client_is_made_with_its_invoice_code_and_used(self, stranger: ScenarioEnv) -> None:
        stranger.readings["timesheet.pdf"] = reading(
            AUG_START, AUG_END, consultant="Sam Okafor", client="Globex"
        )
        reply_to(
            stranger,
            "Needs your review",
            form_reply(
                **{
                    setup.CLIENT: "Globex",
                    setup.CLIENT_EMAIL: "ap@globex.example",
                    setup.CLIENT_PAYS_WITHIN: "45",
                    setup.INVOICE_CODE: "GX",
                }
            ),
        )
        stranger.run()
        _, body = latest(stranger, "Set up in QuickBooks?")
        assert "New client Globex" in body and "invoice code GX" in body
        reply_to(stranger, "Set up in QuickBooks?", f"confirm {the_code(stranger)}")
        stranger.run()

        assert stranger.accounting.customers["Globex"].notes == "Invoice code: GX"
        (sam,) = [i for i in stranger.store.list_items() if i.consultant == "Sam Okafor"]
        assert sam.client == "Globex"
        assert sam.snapshot.client_invoice_code == "GX"
        assert sam.snapshot.payment_terms_days == 45


class TestWhenItCannotBeDone:
    def test_dry_run_says_what_it_would_have_made_and_makes_nothing(
        self, filled_in: ScenarioEnv
    ) -> None:
        filled_in.mode = Mode.DRY_RUN
        reply_to(filled_in, "Set up in QuickBooks?", f"confirm {the_code(filled_in)}")
        filled_in.run()

        assert filled_in.accounting.set_up == []
        subject, body = latest(filled_in, "Dry run — would set up")
        assert "Sam Okafor at Acme Corp" in subject
        assert "changed nothing" in body

    def test_a_refusal_is_reported_and_retried_until_it_works(self, filled_in: ScenarioEnv) -> None:
        filled_in.accounting.fail_setup_with = AccountingFailed("Duplicate Name Exists Error")
        reply_to(filled_in, "Set up in QuickBooks?", f"confirm {the_code(filled_in)}")
        filled_in.run()
        filled_in.run()

        refused = [
            s for s in filled_in.sent_subjects() if s.startswith("Couldn't finish setting up")
        ]
        assert len(refused) == 1  # told once, not once a run
        assert "Duplicate Name Exists Error" in latest(filled_in, "Couldn't finish")[1]

        filled_in.accounting.fail_setup_with = None  # Kevin fixed it in QuickBooks
        filled_in.run()

        assert len(filled_in.accounting.set_up) == 1
        assert any(i.consultant == "Sam Okafor" for i in filled_in.store.list_items())

    def test_try_again_after_a_refusal_is_understood(self, filled_in: ScenarioEnv) -> None:
        filled_in.accounting.fail_setup_with = AccountingFailed("Duplicate Name Exists Error")
        reply_to(filled_in, "Set up in QuickBooks?", f"confirm {the_code(filled_in)}")
        filled_in.run()
        reply_to(filled_in, "Couldn't finish setting up", "try again")
        filled_in.accounting.fail_setup_with = None
        filled_in.run()

        assert len(filled_in.accounting.set_up) == 1
        assert not any("Nothing has been changed" in e.body for e in filled_in.sender.sent_emails())


class TestATimesheetForANewClient:
    def test_the_form_carries_what_the_timesheet_says(self, qbo: ScenarioEnv) -> None:  # noqa: F811
        """Priya is known, but nothing of hers covers July: the engagement the
        timesheet is for was never set up. The form carries what it says."""
        qbo.forwarders = ("tester@example.com",)
        qbo.add_email(
            "tester@example.com",
            scripted_reading=reading(date(2026, 7, 1), date(2026, 7, 31), client="Globex"),
        )
        qbo.run()

        _, body = latest(qbo, "Needs your review")
        assert "Client: Globex" in body
        assert "Consultant: Priya Shah" in body
        assert "Consultant email: tester@example.com" not in body  # a forwarder, not her
