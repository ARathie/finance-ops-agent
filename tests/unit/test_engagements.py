from finance_ops_agent.domain.engagements import (
    ConsultantType,
    ListRowProblem,
    RawRow,
    RawWorkbook,
    parse_workbook,
)
from finance_ops_agent.domain.money import Money
from finance_ops_agent.domain.periods import BillingSchedule


def client_row(row_number: int = 2, **overrides: str) -> RawRow:
    cells = {
        "Client": "Acme Corp",
        "Legal name": "Acme Corporation",
        "Billing contact": "Accounts Payable",
        "Billing email": "ap@acme.example",
        "CC email": "",
        "Payment terms (days)": "30",
        "Delivery": "email",
        "Time system": "Fieldglass",
        "Names on timesheets": "Acme; ACME Corp.",
        "Email domains": "acme.example",
        "QuickBooks customer": "Acme Corporation",
        "Notes": "",
        "Active": "yes",
    }
    cells.update(overrides)
    return RawRow(row_number, cells)


def consultant_row(row_number: int = 2, **overrides: str) -> RawRow:
    cells = {
        "Consultant": "Priya Shah",
        "Other names": "P. Shah; Shah, Priya",
        "Email": "priya@example.com",
        "Type": "contractor",
        "Vendor company": "",
        "Paid by": "bank transfer",
        "Pay timing (days)": "15",
        "Active": "yes",
    }
    cells.update(overrides)
    return RawRow(row_number, cells)


def vendor_row(row_number: int = 2, **overrides: str) -> RawRow:
    cells = {
        "Vendor company": "Blue Peak Consulting LLC",
        "Contact email": "billing@bluepeak.example",
        "Paid by": "bank transfer",
        "Pay timing (days)": "30",
        "Active": "yes",
    }
    cells.update(overrides)
    return RawRow(row_number, cells)


def engagement_row(row_number: int = 2, **overrides: str) -> RawRow:
    cells = {
        "Consultant": "Priya Shah",
        "Client": "Acme Corp",
        "End client": "",
        "Role": "Senior PeopleSoft Developer",
        "Start date": "2026-02-01",
        "End date": "",
        "Billing schedule": "monthly",
        "First period start": "",
        "Bill rate": "140.00",
        "Pay rate": "100.00",
        "Rates from": "2026-02-01",
        "Send automatically": "no",
        "Active": "yes",
    }
    cells.update(overrides)
    return RawRow(row_number, cells)


def workbook(
    clients: list[RawRow] | None = None,
    consultants: list[RawRow] | None = None,
    vendors: list[RawRow] | None = None,
    engagements: list[RawRow] | None = None,
) -> RawWorkbook:
    return RawWorkbook(
        clients=[client_row()] if clients is None else clients,
        consultants=[consultant_row()] if consultants is None else consultants,
        vendors=[] if vendors is None else vendors,
        engagements=[engagement_row()] if engagements is None else engagements,
    )


def problems_on(parsed_problems: list[ListRowProblem], sheet: str, row: int) -> list[str]:
    return [p.message for p in parsed_problems if p.sheet == sheet and p.row_number == row]


class TestGoodWorkbook:
    def test_parses_with_no_problems(self) -> None:
        parsed = parse_workbook(workbook())
        assert parsed.problems == []
        assert len(parsed.clients) == 1
        assert len(parsed.consultants) == 1
        assert len(parsed.engagements) == 1

    def test_typed_values(self) -> None:
        parsed = parse_workbook(workbook())
        client = parsed.clients[0]
        assert client.billing_emails == ("ap@acme.example",)
        assert client.names_on_timesheets == ("Acme", "ACME Corp.")
        assert client.payment_terms_days == 30
        engagement = parsed.engagements[0]
        assert engagement.bill_rate == Money(14_000)
        assert engagement.pay_rate == Money(10_000)
        assert engagement.billing_schedule is BillingSchedule.MONTHLY
        assert not engagement.send_automatically
        consultant = parsed.consultants[0]
        assert consultant.type is ConsultantType.CONTRACTOR
        assert consultant.emails == ("priya@example.com",)


class TestBadRowsFromTheDocs:
    """Each bad row described in docs/engagement-list.md, naming the sheet and row."""

    def test_missing_rate(self) -> None:
        parsed = parse_workbook(workbook(engagements=[engagement_row(5, **{"Bill rate": ""})]))
        assert problems_on(parsed.problems, "Engagements", 5)
        assert parsed.engagements == []

    def test_unknown_client_name(self) -> None:
        parsed = parse_workbook(workbook(engagements=[engagement_row(3, Client="Nonesuch Inc")]))
        messages = problems_on(parsed.problems, "Engagements", 3)
        assert any("Clients sheet" in m for m in messages)
        assert parsed.engagements == []

    def test_unknown_consultant_name(self) -> None:
        parsed = parse_workbook(workbook(engagements=[engagement_row(3, Consultant="Nobody")]))
        assert any("Consultants sheet" in m for m in problems_on(parsed.problems, "Engagements", 3))

    def test_vendor_type_without_vendor_company(self) -> None:
        parsed = parse_workbook(workbook(consultants=[consultant_row(4, Type="vendor")]))
        assert problems_on(parsed.problems, "Consultants", 4)
        assert parsed.consultants == []

    def test_vendor_company_not_on_vendors_sheet(self) -> None:
        parsed = parse_workbook(
            workbook(
                consultants=[consultant_row(4, Type="vendor", **{"Vendor company": "Ghost LLC"})],
                vendors=[vendor_row(2)],
            )
        )
        assert any("Vendors sheet" in m for m in problems_on(parsed.problems, "Consultants", 4))

    def test_no_billing_email_for_a_client_delivered_by_email(self) -> None:
        parsed = parse_workbook(workbook(clients=[client_row(7, **{"Billing email": ""})]))
        assert any("billing email" in m for m in problems_on(parsed.problems, "Clients", 7))
        assert parsed.clients == []

    def test_portal_client_needs_no_billing_email(self) -> None:
        parsed = parse_workbook(
            workbook(clients=[client_row(7, Delivery="portal", **{"Billing email": ""})])
        )
        assert parsed.problems == []

    def test_pay_rate_higher_than_bill_rate(self) -> None:
        parsed = parse_workbook(workbook(engagements=[engagement_row(6, **{"Pay rate": "150.00"})]))
        assert any(
            "higher than the bill rate" in m for m in problems_on(parsed.problems, "Engagements", 6)
        )

    def test_two_rows_with_the_same_consultant_client_and_rates_from(self) -> None:
        parsed = parse_workbook(workbook(engagements=[engagement_row(2), engagement_row(3)]))
        messages = problems_on(parsed.problems, "Engagements", 3)
        assert any("row 2" in m for m in messages)
        assert len(parsed.engagements) == 1


class TestOtherBadCells:
    def test_weekly_without_first_period_start(self) -> None:
        parsed = parse_workbook(
            workbook(engagements=[engagement_row(2, **{"Billing schedule": "weekly"})])
        )
        assert any(
            "First period start" in m for m in problems_on(parsed.problems, "Engagements", 2)
        )

    def test_unparseable_date(self) -> None:
        parsed = parse_workbook(
            workbook(engagements=[engagement_row(2, **{"Start date": "Febuary 1"})])
        )
        assert problems_on(parsed.problems, "Engagements", 2)

    def test_unparseable_money(self) -> None:
        parsed = parse_workbook(workbook(engagements=[engagement_row(2, **{"Bill rate": "a lot"})]))
        assert problems_on(parsed.problems, "Engagements", 2)

    def test_end_date_before_start_date(self) -> None:
        parsed = parse_workbook(
            workbook(engagements=[engagement_row(2, **{"End date": "2026-01-01"})])
        )
        assert problems_on(parsed.problems, "Engagements", 2)

    def test_unknown_schedule(self) -> None:
        parsed = parse_workbook(
            workbook(engagements=[engagement_row(2, **{"Billing schedule": "fortnightly"})])
        )
        assert problems_on(parsed.problems, "Engagements", 2)

    def test_vendor_paid_by_payroll(self) -> None:
        parsed = parse_workbook(workbook(vendors=[vendor_row(3, **{"Paid by": "payroll"})]))
        assert problems_on(parsed.problems, "Vendors", 3)

    def test_bad_yes_no(self) -> None:
        parsed = parse_workbook(workbook(clients=[client_row(2, Active="maybe")]))
        assert problems_on(parsed.problems, "Clients", 2)

    def test_one_email_lists_every_problem_in_a_row(self) -> None:
        parsed = parse_workbook(
            workbook(
                engagements=[engagement_row(2, **{"Bill rate": "", "Start date": "", "Role": ""})]
            )
        )
        assert len(problems_on(parsed.problems, "Engagements", 2)) == 3
