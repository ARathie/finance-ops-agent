"""The engagement list built from QuickBooks alone (decision 53)."""

from datetime import date

import pytest

from finance_ops_agent.adapters.fakes.accounting import FakeAccounting
from finance_ops_agent.application.from_quickbooks import (
    vendors_without_email,
    workbook_from_accounting,
)
from finance_ops_agent.domain.engagements import ConsultantType, Delivery, PaidBy
from finance_ops_agent.domain.money import Money
from finance_ops_agent.ports.accounting import (
    AccountingEngagement,
    AccountingFailed,
    AccountingNeedsReconnect,
    AccountingParty,
)


def product(
    consultant: str = "Priya Shah",
    client: str = "Acme Corp",
    notes: str = "Start: 2026-08-01",
    vendor: str = "7",
    bill: int | None = 14_000,
    pay: int | None = 10_000,
    ref: str = "42",
) -> AccountingEngagement:
    return AccountingEngagement(
        ref=ref,
        consultant=consultant,
        client=client,
        bill_rate_cents=bill,
        pay_rate_cents=pay,
        payee=consultant,
        payee_ref=vendor,
        notes=notes,
    )


def customer(notes: str = "Invoice code: AC", email: str = "ap@acme.example") -> AccountingParty:
    return AccountingParty(
        ref="58",
        name="Acme Corp",
        company="Acme Corporation",
        email=email,
        payment_terms_days=30,
        notes=notes,
    )


def vendor(
    ref: str = "7", name: str = "Priya Shah", email: str = "priya@example.com", company: str = ""
) -> AccountingParty:
    return AccountingParty(ref=ref, name=name, company=company, email=email, payment_terms_days=15)


@pytest.fixture
def company() -> FakeAccounting:
    fake = FakeAccounting()
    fake.live = [product()]
    fake.customers["Acme Corp"] = customer()
    fake.payees["7"] = vendor()
    return fake


def test_everything_the_agent_needs_comes_from_quickbooks(company: FakeAccounting) -> None:
    built = workbook_from_accounting(company)

    assert built.problems == []
    (client,) = built.clients
    assert client.name == "Acme Corp"
    assert client.legal_name == "Acme Corporation"
    assert client.billing_emails == ("ap@acme.example",)
    assert client.payment_terms_days == 30
    assert client.invoice_code == "AC"
    assert client.delivery is Delivery.EMAIL
    (engagement,) = built.engagements
    assert engagement.start_date == date(2026, 8, 1)
    assert engagement.bill_rate == Money(14_000)
    assert engagement.pay_rate == Money(10_000)
    assert engagement.send_automatically is False
    (consultant,) = built.consultants
    assert consultant.emails == ("priya@example.com",)  # the vendor's: who sends timesheets
    assert consultant.type is ConsultantType.VENDOR
    assert consultant.paid_by is PaidBy.BANK_TRANSFER
    assert consultant.pay_timing_days == 15
    (paid,) = built.vendors
    assert paid.company == "Priya Shah"
    assert paid.contact_emails == ()  # on the consultant, so a sender names a person


def test_the_payee_is_the_vendors_company_where_it_has_one(company: FakeAccounting) -> None:
    company.payees["7"] = vendor(company="Blue Peak Consulting LLC")
    built = workbook_from_accounting(company)
    assert built.consultants[0].vendor_company == "Blue Peak Consulting LLC"


def test_no_invoice_code_leaves_the_clients_engagements_out_and_says_why(
    company: FakeAccounting,
) -> None:
    company.customers["Acme Corp"] = customer(notes="")
    built = workbook_from_accounting(company)

    assert built.engagements == []
    assert any("Invoice code" in problem.message for problem in built.problems)
    assert all(problem.sheet == "QuickBooks" for problem in built.problems)


def test_no_start_leaves_that_engagement_out_and_names_the_box(company: FakeAccounting) -> None:
    company.live = [product(), product("Dana Cruz", notes="", vendor="8", ref="43")]
    company.payees["8"] = vendor("8", "Dana Cruz", "dana@example.com")
    built = workbook_from_accounting(company)

    assert [engagement.consultant for engagement in built.engagements] == ["Priya Shah"]
    (problem,) = built.problems
    assert "Dana Cruz" in problem.message
    assert "Description on purchase forms" in problem.message


@pytest.mark.parametrize(
    ("change", "said"),
    [
        ({"vendor": ""}, "no preferred vendor"),
        ({"pay": None}, "no cost on its purchase side"),
        ({"bill": None}, "no sales price"),
    ],
)
def test_a_product_that_is_not_filled_in_is_a_problem(
    company: FakeAccounting, change: dict[str, object], said: str
) -> None:
    company.live = [product(**change)]  # type: ignore[arg-type]
    built = workbook_from_accounting(company)
    assert built.engagements == []
    assert said in built.problems[0].message


def test_a_category_with_no_customer_says_how_to_fix_it(company: FakeAccounting) -> None:
    company.customers.clear()
    built = workbook_from_accounting(company)
    assert built.engagements == []
    assert "Name the category exactly as the customer's" in built.problems[0].message


def test_two_clients_with_one_invoice_code_are_both_refused(company: FakeAccounting) -> None:
    company.live = [product(), product(client="Northwind", ref="44")]
    company.customers["Northwind"] = AccountingParty(
        ref="59",
        name="Northwind",
        email="ap@northwind.example",
        payment_terms_days=30,
        notes="Invoice code: AC",
    )
    built = workbook_from_accounting(company)
    assert any("both have Invoice code AC" in problem.message for problem in built.problems)
    assert built.engagements == []  # neither is billed until one is changed


def test_a_consultant_at_two_clients_is_one_consultant_with_two_engagements(
    company: FakeAccounting,
) -> None:
    company.live = [product(), product(client="Northwind", ref="44")]
    company.customers["Northwind"] = AccountingParty(
        ref="59",
        name="Northwind",
        email="ap@northwind.example",
        payment_terms_days=30,
        notes="Invoice code: NW",
    )
    built = workbook_from_accounting(company)
    assert built.problems == []
    assert len(built.consultants) == 1
    assert len(built.engagements) == 2


def test_a_vendor_with_no_email_is_a_to_do(company: FakeAccounting) -> None:
    company.payees["7"] = vendor(email="")
    built = workbook_from_accounting(company)
    assert built.problems == []
    assert vendors_without_email(built) == ["Priya Shah"]


def test_quickbooks_refusing_one_customer_is_a_problem_not_a_crash(
    company: FakeAccounting,
) -> None:
    def refuse(name: str) -> AccountingParty | None:
        raise AccountingFailed("two customers share that company name")

    company.customer = refuse  # type: ignore[method-assign]
    built = workbook_from_accounting(company)
    assert "two customers share that company name" in built.problems[0].message


def test_a_dead_connection_is_raised_so_the_run_can_say_so(company: FakeAccounting) -> None:
    company.fail_with = AccountingNeedsReconnect("reconnect")
    with pytest.raises(AccountingFailed):
        workbook_from_accounting(company)
