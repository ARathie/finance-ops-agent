"""Whole runs with no spreadsheet at all: FOPS_ENGAGEMENTS=quickbooks (decision 53)."""

from datetime import date

import pytest

from finance_ops_agent.domain.engagements import RawWorkbook
from finance_ops_agent.domain.statuses import ItemStatus
from finance_ops_agent.ports.accounting import (
    AccountingEngagement,
    AccountingFailed,
    AccountingParty,
    EngagementRates,
)
from tests.scenarios.conftest import PRIYA, ScenarioEnv, default_workbook, reading

AUG_START, AUG_END = date(2026, 8, 1), date(2026, 8, 31)
FIRM = "timesheets@bluepeak.example"


def product(consultant: str, ref: str, vendor: str, notes: str = "Start: 2026-08-01"):  # type: ignore[no-untyped-def]
    return AccountingEngagement(
        ref=ref,
        consultant=consultant,
        client="Acme Corp",
        bill_rate_cents=14_000,
        pay_rate_cents=10_000,
        payee=consultant,
        payee_ref=vendor,
        notes=notes,
    )


@pytest.fixture
def qbo(env: ScenarioEnv) -> ScenarioEnv:
    env.engagements_from = "quickbooks"
    env.workbook = RawWorkbook(clients=[], consultants=[], vendors=[], engagements=[])
    env.accounting.live = [product("Priya Shah", "42", "7")]
    env.accounting.customers["Acme Corp"] = AccountingParty(
        ref="58",
        name="Acme Corp",
        company="Acme Corporation",
        email="ap@acme.example",
        payment_terms_days=30,
        notes="Invoice code: AC",
    )
    env.accounting.payees["7"] = AccountingParty(
        ref="7", name="Priya Shah", email=PRIYA, payment_terms_days=15
    )
    env.accounting.rates[("Priya Shah", "Acme Corp")] = EngagementRates(
        ref="42", bill_rate_cents=14_000, pay_rate_cents=10_000, payee="Priya Shah", payee_ref="7"
    )
    return env


def test_a_timesheet_from_the_vendors_email_is_invoiced_with_no_spreadsheet(
    qbo: ScenarioEnv,
) -> None:
    qbo.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END))
    qbo.run()

    item = qbo.the_item()
    assert item.status is ItemStatus.READY
    assert item.consultant == "Priya Shah"
    assert item.snapshot.client_invoice_code == "AC"
    assert item.snapshot.billing_emails == ["ap@acme.example"]
    assert item.snapshot.bill_rate_cents == 14_000
    assert item.snapshot.pay_timing_days == 15
    assert item.snapshot.paid_by == "bank transfer"
    assert item.engagement_ref == "42"
    assert qbo.store.open_reviews() == []


def test_a_month_with_no_timesheet_yet_is_expected_from_the_start_line(qbo: ScenarioEnv) -> None:
    qbo.run()

    item = qbo.the_item()
    assert item.status is ItemStatus.WAITING_FOR_TIMESHEET
    assert (item.period.start, item.period.end) == (AUG_START, AUG_END)


def test_a_vendor_with_no_email_means_an_unknown_sender(qbo: ScenarioEnv) -> None:
    qbo.accounting.payees["7"] = AccountingParty(
        ref="7", name="Priya Shah", email="", payment_terms_days=15
    )
    qbo.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END))
    qbo.run()

    assert any(review.code == "UNKNOWN_SENDER" for review in qbo.store.open_reviews())


def test_forwarding_still_works_while_the_vendor_email_is_blank(qbo: ScenarioEnv) -> None:
    qbo.accounting.payees["7"] = AccountingParty(
        ref="7", name="Priya Shah", email="", payment_terms_days=15
    )
    qbo.forwarders = ("tester@example.com",)
    qbo.add_email("tester@example.com", scripted_reading=reading(AUG_START, AUG_END))
    qbo.run()

    assert qbo.the_item().status is ItemStatus.READY


def test_something_missing_in_quickbooks_is_a_review_naming_the_record(qbo: ScenarioEnv) -> None:
    qbo.accounting.customers["Acme Corp"] = AccountingParty(
        ref="58", name="Acme Corp", email="ap@acme.example", payment_terms_days=30, notes=""
    )
    qbo.run()

    (review,) = [r for r in qbo.store.open_reviews() if r.code == "LIST_ROW_PROBLEM"]
    assert review.message.startswith("QuickBooks: customer Acme Corp:")
    assert "Invoice code" in review.message
    assert qbo.store.list_items() == []  # nothing is billed from half an answer


def test_quickbooks_down_falls_back_to_the_engagement_list(qbo: ScenarioEnv) -> None:
    qbo.workbook = default_workbook()
    qbo.accounting.fail_with = AccountingFailed("service unavailable")
    qbo.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END))
    report = qbo.run()

    # Read from the list, and waiting on QuickBooks for its rates (decision 55).
    assert qbo.the_item().status is ItemStatus.NEEDS_REVIEW
    assert any("used the engagement list" in line for line in report.lines)


class TestOneAddressForTwoConsultants:
    """A firm that sends two people's timesheets from its own address: the
    name on the timesheet chooses between them, never the first one listed."""

    @pytest.fixture
    def firm(self, qbo: ScenarioEnv) -> ScenarioEnv:
        qbo.accounting.live = [product("Priya Shah", "42", "7"), product("Dana Cruz", "43", "7")]
        qbo.accounting.payees["7"] = AccountingParty(
            ref="7",
            name="Blue Peak",
            company="Blue Peak Consulting LLC",
            email=FIRM,
            payment_terms_days=30,
        )
        for consultant, ref in (("Priya Shah", "42"), ("Dana Cruz", "43")):
            qbo.accounting.rates[(consultant, "Acme Corp")] = EngagementRates(
                ref=ref,
                bill_rate_cents=14_000,
                pay_rate_cents=10_000,
                payee="Blue Peak Consulting LLC",
                payee_ref="7",
            )
        return qbo

    def test_the_name_on_the_timesheet_decides(self, firm: ScenarioEnv) -> None:
        firm.add_email(FIRM, scripted_reading=reading(AUG_START, AUG_END, consultant="Dana Cruz"))
        firm.run()

        received = [item for item in firm.store.list_items() if item.status is ItemStatus.READY]
        assert [item.consultant for item in received] == ["Dana Cruz"]

    def test_a_name_that_is_neither_is_asked_about(self, firm: ScenarioEnv) -> None:
        firm.add_email(FIRM, scripted_reading=reading(AUG_START, AUG_END, consultant="Sam Okafor"))
        firm.run()

        (review,) = [r for r in firm.store.open_reviews() if r.code == "CONSULTANT_UNKNOWN"]
        assert "Priya Shah, Dana Cruz" in review.message
        assert not any(item.status is ItemStatus.READY for item in firm.store.list_items())


def test_a_customer_with_no_email_is_still_a_client_and_its_invoice_waits(
    qbo: ScenarioEnv,
) -> None:
    """Decision 65: before, the customer was left out of the copy, so its
    timesheets looked like ones for a client nobody knew."""
    held = qbo.accounting.customers["Acme Corp"]
    qbo.accounting.customers["Acme Corp"] = AccountingParty(
        ref=held.ref,
        name=held.name,
        company=held.company,
        email="",
        payment_terms_days=held.payment_terms_days,
        notes=held.notes,
    )
    qbo.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END))
    qbo.run()

    item = qbo.the_item()
    assert item.status is ItemStatus.NEEDS_REVIEW
    [review] = [r for r in qbo.store.open_reviews() if r.code == "NO_BILLING_CONTACT"]
    assert "the Acme Corp customer in QuickBooks" in review.message

    # Kevin adds the address in QuickBooks; the next day's copy has it.
    qbo.accounting.customers["Acme Corp"] = held
    qbo.today = date(2026, 9, 9)
    qbo.run()

    item = qbo.store.get_item(item.id)
    assert item.snapshot.billing_emails == ["ap@acme.example"]
    assert item.status is not ItemStatus.NEEDS_REVIEW
    assert not [r for r in qbo.store.open_reviews() if r.code == "NO_BILLING_CONTACT"]
