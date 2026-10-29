"""The engagement list built from QuickBooks alone (decision 53)."""

from datetime import date

import pytest

from finance_ops_agent.adapters.fakes.accounting import FakeAccounting
from finance_ops_agent.application.from_quickbooks import (
    assumed_start,
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


class TestASalariedEmployee:
    """Decisions 69 and 70: a product with nothing on its purchase side is an
    Icon employee on a salary. Their address is on their QuickBooks employee
    record, and a start date QuickBooks has no field for is remembered."""

    def dana(self, company: FakeAccounting, **changes: object) -> None:
        fields: dict[str, object] = {
            "consultant": "Dana Cruz",
            "vendor": "",
            "pay": None,
            "notes": "",
            "ref": "44",
        }
        fields.update(changes)
        company.live = [product(**fields)]  # type: ignore[arg-type]
        company.employee_records = [
            AccountingParty(
                ref="e1", name="Dana Cruz", email="dana@example.com", payment_terms_days=None
            )
        ]

    def test_nothing_on_the_purchase_side_is_a_salaried_employee(
        self, company: FakeAccounting
    ) -> None:
        self.dana(company)
        built = workbook_from_accounting(company, date(2026, 9, 8), {})

        assert built.problems == []
        (consultant,) = built.consultants
        assert consultant.salaried
        assert consultant.type is ConsultantType.EMPLOYEE
        assert consultant.emails == ("dana@example.com",)  # off the employee record
        (engagement,) = built.engagements
        assert engagement.pay_rate is None
        assert engagement.bill_rate == Money(14_000)
        assert built.vendors == []

    def test_no_start_date_means_last_month_and_it_is_remembered(
        self, company: FakeAccounting
    ) -> None:
        self.dana(company)
        starts: dict[str, str] = {}
        built = workbook_from_accounting(company, date(2026, 9, 8), starts)

        assert built.engagements[0].start_date == date(2026, 8, 1)
        assert starts == {"44": "2026-08-01"}
        # A month later it is still August: the assumption does not move.
        later = workbook_from_accounting(company, date(2026, 10, 20), starts)
        assert later.engagements[0].start_date == date(2026, 8, 1)

    def test_last_month_across_new_year(self) -> None:
        assert assumed_start(date(2027, 1, 5)) == date(2026, 12, 1)

    def test_a_start_line_still_counts_if_one_was_written(self, company: FakeAccounting) -> None:
        self.dana(company, notes="Start: 2026-06-01")
        starts: dict[str, str] = {}
        built = workbook_from_accounting(company, date(2026, 9, 8), starts)

        assert built.engagements[0].start_date == date(2026, 6, 1)
        assert starts == {}

    def test_without_a_date_to_assume_from_it_is_a_problem(self, company: FakeAccounting) -> None:
        self.dana(company)
        built = workbook_from_accounting(company)

        assert built.engagements == []
        assert any("when this salaried engagement started" in p.message for p in built.problems)

    def test_a_cost_left_blank_but_held_as_zero_is_still_no_cost(
        self, company: FakeAccounting
    ) -> None:
        self.dana(company, pay=0)
        built = workbook_from_accounting(company, date(2026, 9, 8), {})

        assert built.problems == []
        assert built.consultants[0].salaried

    def test_no_employee_record_is_a_to_do(self, company: FakeAccounting) -> None:
        self.dana(company)
        company.employee_records = []
        built = workbook_from_accounting(company, date(2026, 9, 8), {})

        assert built.problems == []
        assert vendors_without_email(built) == ["Dana Cruz"]

    def test_the_record_is_found_under_given_and_family_name(self, company: FakeAccounting) -> None:
        self.dana(company)
        company.employee_records = [
            AccountingParty(
                ref="e1",
                name="Dana C.",
                company="Dana Cruz",
                email="dana@example.com",
                payment_terms_days=None,
            )
        ]
        built = workbook_from_accounting(company, date(2026, 9, 8), {})

        assert built.consultants[0].emails == ("dana@example.com",)

    def test_two_employees_by_that_name_are_a_question(self, company: FakeAccounting) -> None:
        self.dana(company)
        company.employee_records = company.employee_records * 2
        built = workbook_from_accounting(company, date(2026, 9, 8), {})

        assert built.engagements == []
        assert any("2 employees called Dana Cruz" in p.message for p in built.problems)

    def test_employees_are_not_asked_for_when_nobody_is_salaried(
        self, company: FakeAccounting
    ) -> None:
        workbook_from_accounting(company, date(2026, 9, 8), {})
        assert company.calls["employees"] == 0

    def test_a_cost_with_no_vendor_is_a_question(self, company: FakeAccounting) -> None:
        company.live = [product(consultant="Dana Cruz", vendor="")]
        built = workbook_from_accounting(company, date(2026, 9, 8), {})

        assert built.engagements == []
        assert any(
            "a cost on its purchase side but no preferred vendor" in p.message
            for p in built.problems
        )

    def test_a_vendor_on_one_product_and_not_another_is_a_question(
        self, company: FakeAccounting
    ) -> None:
        company.customers["Beta LLC"] = AccountingParty(
            ref="59",
            name="Beta LLC",
            company="",
            email="ap@beta.example",
            payment_terms_days=30,
            notes="Invoice code: BE",
        )
        company.live = [product(), product(client="Beta LLC", vendor="", pay=None, ref="43")]
        built = workbook_from_accounting(company, date(2026, 9, 8), {})

        assert len(built.engagements) == 1
        assert any(
            "a vendor on one product and none on another" in p.message for p in built.problems
        )
