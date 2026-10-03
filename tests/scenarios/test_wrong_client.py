"""Right consultant, wrong client (decision 57).

Priya starts at a second client while still at Acme. Her first timesheet for
the new client covers dates only her Acme engagement covers, so the agent puts
it on Acme -- and, in ask-first mode, Kevin sees the draft before the client
does. He replies "wrong client": the draft is voided, Acme goes back to waiting
for its own timesheet, and the setup form makes the right engagement, whose
invoice then comes to him for approval in its turn.
"""

import re
from datetime import date

import pytest

from finance_ops_agent.application.run import Mode
from finance_ops_agent.domain import setup
from finance_ops_agent.domain.reading import ReplyAnswer, ReplyAnswerKind, ReplyReading
from finance_ops_agent.domain.statuses import ItemStatus
from finance_ops_agent.ports.accounting import (
    AccountingEngagement,
    AccountingParty,
    EngagementRates,
)
from tests.scenarios.conftest import PRIYA, ScenarioEnv, reading
from tests.scenarios.test_engagement_copy import add_to_quickbooks, qbo  # noqa: F401

AUG_START, AUG_END = date(2026, 8, 1), date(2026, 8, 31)


def latest(env: ScenarioEnv, subject_starts: str) -> tuple[str, str]:
    email = [e for e in env.sender.sent_emails() if e.subject.startswith(subject_starts)][-1]
    return email.subject, email.body


def reply_to(env: ScenarioEnv, subject_starts: str, body: str) -> None:
    subject, _ = latest(env, subject_starts)
    env.reply_from_kevin(subject, body)


def item_for(env: ScenarioEnv, client: str) -> list:  # type: ignore[type-arg]
    return [i for i in env.store.list_items() if i.client == client]


def globex_form() -> str:
    values = {
        setup.CONSULTANT: "Priya Shah",
        setup.CONSULTANT_EMAIL: PRIYA,
        setup.PAY_WITHIN: "15",
        setup.CLIENT: "Globex",
        setup.CLIENT_EMAIL: "ap@globex.example",
        setup.CLIENT_PAYS_WITHIN: "45",
        setup.INVOICE_CODE: "GX",
        setup.BILL_RATE: "160.00",
        setup.PAY_RATE: "120.00",
        setup.START: "2026-08-01",
    }
    return "\n".join(f"{label}: {value}" for label, value in values.items())


@pytest.fixture
def misbilled(qbo: ScenarioEnv) -> ScenarioEnv:  # noqa: F811
    """Priya's Globex timesheet, put on Acme and waiting for Kevin's approval."""
    qbo.mode = Mode.ASK_FIRST
    qbo.add_email(
        PRIYA,
        attachment=("globex-aug.pdf", b"GLOBEX-AUG"),
        scripted_reading=reading(AUG_START, AUG_END, client="Globex"),
    )
    qbo.run()
    (acme,) = item_for(qbo, "Acme Corp")
    assert acme.status is ItemStatus.WAITING_FOR_APPROVAL
    return qbo


@pytest.fixture
def wrong(misbilled: ScenarioEnv) -> ScenarioEnv:
    reply_to(misbilled, "Approve?", "Wrong client - this is a new one")
    misbilled.run()
    return misbilled


class TestTheApprovalEmail:
    def test_it_offers_wrong_client(self, misbilled: ScenarioEnv) -> None:
        _, body = latest(misbilled, "Approve?")
        assert 'Reply "wrong client"' in body


class TestKevinSaysWrongClient:
    def test_the_draft_is_voided(self, wrong: ScenarioEnv) -> None:
        (cancelled,) = wrong.accounting.cancelled
        assert wrong.accounting.renamed[cancelled].endswith("-VOID")

    def test_acme_goes_back_to_waiting_for_its_own_timesheet(self, wrong: ScenarioEnv) -> None:
        (acme,) = item_for(wrong, "Acme Corp")
        assert acme.status is ItemStatus.WAITING_FOR_TIMESHEET
        assert acme.approved_hours is None and acme.invoice_amount is None
        assert wrong.store.timesheets_for_item(acme.id) == []
        last = wrong.store.audit_entries(acme.id)[-1]
        assert last.details["why"] == "Kevin said the timesheet is not for Acme Corp"

    def test_nothing_reached_the_client_or_the_consultant(self, wrong: ScenarioEnv) -> None:
        assert not any(e.to == ("ap@acme.example",) for e in wrong.sender.sent_emails())
        assert not any(s.startswith("Payment due") for s in wrong.sent_subjects())

    def test_he_gets_the_form_with_what_is_known(self, wrong: ScenarioEnv) -> None:
        _, body = latest(wrong, "Needs your review")
        assert "is not for Acme Corp" in body
        assert "Consultant: Priya Shah" in body
        assert f"Consultant email: {PRIYA}" in body
        assert "Start date: 2026-08-01" in body
        assert re.search(r"^Client: \s*$", body, re.MULTILINE)  # his to fill in

    def test_after_approval_it_is_too_late_and_nothing_changes(
        self, misbilled: ScenarioEnv
    ) -> None:
        reply_to(misbilled, "Approve?", "approve")
        misbilled.run()
        reply_to(misbilled, "Approve?", "wrong client")
        misbilled.run()

        (acme,) = item_for(misbilled, "Acme Corp")
        assert acme.status is ItemStatus.INVOICE_SENT
        assert misbilled.accounting.cancelled == []
        assert "haven't changed anything" in misbilled.sender.sent_emails()[-1].body


class TestTheRightClientIsSetUp:
    @pytest.fixture
    def set_up(self, wrong: ScenarioEnv) -> ScenarioEnv:
        reply_to(wrong, "Needs your review", globex_form())
        wrong.run()
        _, body = latest(wrong, "Set up in QuickBooks?")
        code = re.search(r'"confirm (\d{4})"', body)
        assert code is not None
        reply_to(wrong, "Set up in QuickBooks?", f"confirm {code.group(1)}")
        wrong.run()
        return wrong

    def test_the_timesheet_goes_through_again_under_the_new_client(
        self, set_up: ScenarioEnv
    ) -> None:
        (globex,) = item_for(set_up, "Globex")
        assert globex.status is ItemStatus.WAITING_FOR_APPROVAL
        assert globex.snapshot.bill_rate_cents == 16_000  # the new product's
        assert globex.snapshot.client_invoice_code == "GX"
        subject, _ = latest(set_up, "Approve?")
        assert subject.startswith("Approve? Invoice for Globex — Priya Shah")
        (record,) = set_up.store.invoices_for_item(globex.id)
        assert "GX-PS" in record.number

    def test_acme_still_waits_and_nothing_is_open(self, set_up: ScenarioEnv) -> None:
        (acme,) = item_for(set_up, "Acme Corp")
        assert acme.status is ItemStatus.WAITING_FOR_TIMESHEET
        assert set_up.store.open_reviews() == []

    def test_her_real_acme_timesheet_then_fills_acme(self, set_up: ScenarioEnv) -> None:
        set_up.add_email(
            PRIYA,
            attachment=("acme-aug.pdf", b"ACME-AUG"),
            scripted_reading=reading(AUG_START, AUG_END, total_hundredths=8_000),
        )
        set_up.run()

        (acme,) = item_for(set_up, "Acme Corp")
        assert acme.status is ItemStatus.WAITING_FOR_APPROVAL
        assert acme.approved_hours is not None and acme.approved_hours.hundredths == 8_000
        (globex,) = item_for(set_up, "Globex")
        assert globex.status is ItemStatus.WAITING_FOR_APPROVAL  # untouched


class TestTheRightClientAlreadyExists:
    def test_try_again_after_setting_it_up_by_hand(self, wrong: ScenarioEnv) -> None:
        """Kevin adds Priya's Globex engagement in QuickBooks himself. Both
        engagements now cover August; the one he ruled out is never used."""
        wrong.accounting.customers["Globex"] = AccountingParty(
            ref="60",
            name="Globex",
            email="ap@globex.example",
            payment_terms_days=30,
            notes="Invoice code: GX",
        )
        wrong.accounting.live.append(
            AccountingEngagement(
                ref="44",
                consultant="Priya Shah",
                client="Globex",
                bill_rate_cents=16_000,
                pay_rate_cents=12_000,
                payee="Priya Shah",
                payee_ref="7",
                notes="Start: 2026-08-01",
            )
        )
        wrong.accounting.rates[("Priya Shah", "Globex")] = EngagementRates(
            ref="44",
            bill_rate_cents=16_000,
            pay_rate_cents=12_000,
            payee="Priya Shah",
            payee_ref="7",
        )
        wrong.replies["try again"] = ReplyReading(
            answers=[ReplyAnswer(review_code="ENGAGEMENT_UNCLEAR", kind=ReplyAnswerKind.TRY_AGAIN)]
        )
        reply_to(wrong, "Needs your review", "try again")
        wrong.run()

        (globex,) = item_for(wrong, "Globex")
        assert globex.status is ItemStatus.WAITING_FOR_APPROVAL
        (acme,) = item_for(wrong, "Acme Corp")
        assert acme.status is ItemStatus.WAITING_FOR_TIMESHEET

    def test_try_again_with_nothing_new_never_puts_it_back_on_acme(
        self, wrong: ScenarioEnv
    ) -> None:
        wrong.replies["try again"] = ReplyReading(
            answers=[ReplyAnswer(review_code="ENGAGEMENT_UNCLEAR", kind=ReplyAnswerKind.TRY_AGAIN)]
        )
        reply_to(wrong, "Needs your review", "try again")
        wrong.run()

        (acme,) = item_for(wrong, "Acme Corp")
        assert acme.status is ItemStatus.WAITING_FOR_TIMESHEET
        assert wrong.store.timesheets_for_item(acme.id) == []
        assert any(
            "is not for Acme Corp" in r.message or "not for Acme Corp" in r.message
            for r in wrong.store.open_reviews()
        )


class TestATimesheetThatNamesAnEndClient:
    def test_the_client_kevin_set_up_decides(self, qbo: ScenarioEnv) -> None:  # noqa: F811
        """The timesheet names the plant Priya works at, not Icon's client, so
        once both engagements cover August only Kevin's answer can choose."""
        qbo.mode = Mode.ASK_FIRST
        qbo.add_email(
            PRIYA,
            attachment=("plant.pdf", b"PLANT-AUG"),
            scripted_reading=reading(AUG_START, AUG_END, client="Initech Plant 4"),
        )
        qbo.run()
        reply_to(qbo, "Approve?", "wrong client")
        qbo.run()
        reply_to(qbo, "Needs your review", globex_form())
        qbo.run()
        _, body = latest(qbo, "Set up in QuickBooks?")
        code = re.search(r'"confirm (\d{4})"', body)
        assert code is not None
        reply_to(qbo, "Set up in QuickBooks?", f"confirm {code.group(1)}")
        qbo.run()

        (globex,) = item_for(qbo, "Globex")
        assert globex.status is ItemStatus.WAITING_FOR_APPROVAL
        assert qbo.store.open_reviews() == []
