"""The QuickBooks Online adapter setting an engagement up (decision 56).

Driven against a stub of the HTTP client that answers queries from a small
in-memory company and records every write, so what is sent can be checked
field by field. No network, no credentials.
"""

from datetime import date
from typing import Any

import pytest

from finance_ops_agent.adapters.quickbooks.client import QuickBooksFailed
from finance_ops_agent.adapters.quickbooks.online import QuickBooksOnline
from finance_ops_agent.application.from_quickbooks import workbook_from_accounting
from finance_ops_agent.domain.engagements import EngagementWorkbook
from finance_ops_agent.domain.money import Money
from finance_ops_agent.domain.setup import EngagementSetup

TODAY = date(2026, 9, 8)
ACCOUNTS = ({"value": "79", "name": "Services"}, {"value": "80", "name": "Contractors"})


class StubCompany:
    """Just enough of QuickBooksClient: query, post, company_url."""

    def __init__(self) -> None:
        self.rows: dict[str, list[dict[str, Any]]] = {
            "Term": [{"Id": "3", "DueDays": 30}],
            "Customer": [],
            "Vendor": [],
            "Item": [
                {
                    "Id": "40",
                    "Name": "Priya Shah",
                    "FullyQualifiedName": "Acme Corp:Priya Shah",
                    "Type": "Service",
                    "ParentRef": {"value": "39"},
                    "IncomeAccountRef": ACCOUNTS[0],
                    "ExpenseAccountRef": ACCOUNTS[1],
                    "UnitPrice": 140,
                }
            ],
            "Preferences": [],
        }
        self.queries: list[str] = []
        self.posts: list[tuple[str, dict[str, Any]]] = []
        self._next = 100

    def company_url(self, suffix: str) -> str:
        return f"https://qb.example/v3/company/1{suffix}"

    def query(self, statement: str) -> list[dict[str, Any]]:
        self.queries.append(statement)
        entity = statement.split(" FROM ")[1].split()[0]
        rows = self.rows.get(entity, [])
        if " WHERE " not in statement:
            return list(rows)
        condition = statement.split(" WHERE ")[1].split(" STARTPOSITION")[0]
        condition = condition.split(" MAXRESULTS")[0]
        if condition == "Active = true":
            return list(rows)
        field, _, value = condition.partition(" = ")
        wanted = value.strip("'").replace("\\'", "'")
        return [row for row in rows if str(row.get(field)) == wanted]

    def post(self, url: str, json: Any = None) -> Any:
        self.posts.append((url, json))
        entity = url.split("/company/1/")[1].split("?")[0].capitalize()
        if json.get("sparse"):
            return {entity: json}
        self._next += 1
        row = {**json, "Id": str(self._next), "SyncToken": "0", "Active": True}
        # What QuickBooks itself fills in on the way back: the full name of a
        # product under a category, and the name beside every reference.
        if entity == "Item":
            row["FullyQualifiedName"] = json["Name"]
        if entity == "Item" and "ParentRef" in json:
            parent = self._by_id("Item", json["ParentRef"]["value"])
            row["FullyQualifiedName"] = f"{parent['Name']}:{json['Name']}"
            row["ParentRef"] = {**json["ParentRef"], "name": parent["Name"]}
        if "PrefVendorRef" in json:
            vendor = self._by_id("Vendor", json["PrefVendorRef"]["value"])
            row["PrefVendorRef"] = {**json["PrefVendorRef"], "name": vendor["DisplayName"]}
        for field in ("SalesTermRef", "TermRef"):
            if field in json:
                term = self._by_id("Term", json[field]["value"])
                row[field] = {**json[field], "name": term.get("Name", "")}
        self.rows.setdefault(entity, []).append(row)
        return {entity: row}

    def _by_id(self, entity: str, reference: str) -> dict[str, Any]:
        return next(row for row in self.rows[entity] if row["Id"] == reference)

    def created(self, entity: str) -> list[dict[str, Any]]:
        return [body for url, body in self.posts if f"/{entity}?" in url and "sparse" not in body]


def plan(**changes: Any) -> EngagementSetup:
    values: dict[str, Any] = {
        "consultant": "Sam Okafor",
        "consultant_email": "sam@example.com",
        "firm": "Okafor Consulting LLC",
        "pay_within_days": 15,
        "client": "Globex",
        "new_client": True,
        "client_legal_name": "Globex Corporation",
        "client_email": "ap@globex.example",
        "client_pays_within_days": 30,
        "invoice_code": "GX",
        "bill_rate": Money(15_000),
        "pay_rate": Money(11_000),
        "start": date(2026, 9, 1),
    }
    values.update(changes)
    return EngagementSetup(**values)


def adapter(company: StubCompany) -> QuickBooksOnline:
    return QuickBooksOnline(company, TODAY)  # type: ignore[arg-type]


class TestANewClientAndConsultant:
    def test_everything_is_made_with_the_fields_the_agent_reads_back(self) -> None:
        company = StubCompany()
        done = adapter(company).set_up_engagement(plan())

        (customer,) = company.created("customer")
        assert customer["DisplayName"] == "Globex"
        assert customer["CompanyName"] == "Globex Corporation"
        assert customer["PrimaryEmailAddr"] == {"Address": "ap@globex.example"}
        assert customer["SalesTermRef"] == {"value": "3"}  # the existing 30-day term
        assert customer["Notes"] == "Invoice code: GX"

        category, product = company.created("item")
        assert category == {"Name": "Globex", "Type": "Category"}

        (vendor,) = company.created("vendor")
        assert vendor["DisplayName"] == "Sam Okafor"
        assert vendor["CompanyName"] == "Okafor Consulting LLC"
        assert vendor["PrimaryEmailAddr"] == {"Address": "sam@example.com"}

        assert product["Name"] == "Sam Okafor"
        assert product["SubItem"] is True
        assert product["UnitPrice"] == 150.0
        assert product["PurchaseCost"] == 110.0
        assert product["PurchaseDesc"] == "Start: 2026-09-01"
        assert product["PrefVendorRef"] == {"value": company.rows["Vendor"][0]["Id"]}
        assert (product["IncomeAccountRef"], product["ExpenseAccountRef"]) == ACCOUNTS
        assert "product Globex:Sam Okafor" in done.created

    def test_a_term_with_no_match_is_made(self) -> None:
        company = StubCompany()
        adapter(company).set_up_engagement(plan())  # vendor pays within 15: no such term

        (term,) = company.created("term")
        assert term == {"Name": "Net 15", "DueDays": 15}
        (vendor,) = company.created("vendor")
        assert vendor["TermRef"] == {"value": str(int(company.rows["Term"][-1]["Id"]))}

    def test_every_write_asks_for_a_shape_that_has_categories(self) -> None:
        company = StubCompany()
        adapter(company).set_up_engagement(plan())
        assert all("minorversion=75" in url for url, _ in company.posts)


class TestRunningItAgain:
    def test_a_second_run_finds_everything_and_makes_nothing(self) -> None:
        company = StubCompany()
        adapter(company).set_up_engagement(plan())
        made = len(company.posts)

        done = adapter(company).set_up_engagement(plan())

        assert len(company.posts) == made
        assert done.created == []
        assert set(done.reused) == {
            "customer Globex",
            "category Globex",
            "vendor Sam Okafor",
            "product Globex:Sam Okafor",
        }


class TestWhatItWillNotDo:
    def test_an_existing_vendor_with_another_address_is_refused(self) -> None:
        company = StubCompany()
        company.rows["Vendor"].append(
            {
                "Id": "7",
                "SyncToken": "2",
                "DisplayName": "Sam Okafor",
                "PrimaryEmailAddr": {"Address": "old@example.com"},
            }
        )
        with pytest.raises(QuickBooksFailed, match="old@example.com"):
            adapter(company).set_up_engagement(plan())
        assert company.created("item")[1:] == []  # no product made

    def test_an_existing_vendor_with_no_address_gets_this_one(self) -> None:
        company = StubCompany()
        company.rows["Vendor"].append({"Id": "7", "SyncToken": "2", "DisplayName": "Sam Okafor"})
        adapter(company).set_up_engagement(plan())

        sparse = [body for _, body in company.posts if body.get("sparse")]
        assert sparse == [
            {
                "Id": "7",
                "SyncToken": "2",
                "sparse": True,
                "PrimaryEmailAddr": {"Address": "sam@example.com"},
            }
        ]

    def test_a_product_already_there_at_another_rate_is_refused(self) -> None:
        company = StubCompany()
        company.rows["Customer"].append({"Id": "58", "SyncToken": "0", "DisplayName": "Acme Corp"})
        with pytest.raises(QuickBooksFailed, match="won't change a rate"):
            adapter(company).set_up_engagement(
                plan(consultant="Priya Shah", client="Acme Corp", new_client=False)
            )

    def test_with_no_engagement_to_copy_accounts_from_it_stops_before_the_product(
        self,
    ) -> None:
        company = StubCompany()
        company.rows["Item"] = []
        with pytest.raises(QuickBooksFailed, match="income and an expense account"):
            adapter(company).set_up_engagement(plan())
        assert [b for b in company.created("item") if b.get("Type") == "Service"] == []

    def test_an_existing_client_that_is_not_there_is_refused(self) -> None:
        company = StubCompany()
        with pytest.raises(QuickBooksFailed, match="has no customer"):
            adapter(company).set_up_engagement(plan(new_client=False))


class TestAClientQuickBooksHadAllAlong:
    def test_it_is_used_and_given_its_invoice_code(self) -> None:
        """Kevin said new, but the customer was there with no invoice code,
        which is why the agent never saw it."""
        company = StubCompany()
        company.rows["Customer"].append(
            {"Id": "58", "SyncToken": "4", "DisplayName": "Globex", "Notes": "Net 30 always"}
        )
        done = adapter(company).set_up_engagement(plan())

        assert company.created("customer") == []
        sparse = [body for _, body in company.posts if body.get("sparse")]
        assert sparse[0]["Notes"] == "Net 30 always\nInvoice code: GX"
        assert "customer Globex" in done.reused


def company_as_icon_has_it() -> StubCompany:
    """Acme Corp with Priya Shah, set up by hand the way Kevin has done it
    (tests/fixtures/qbo/from_quickbooks.json, quickbooks-setup.md)."""
    company = StubCompany()
    company.rows["Term"] = [
        {"Id": "3", "Name": "Net 30", "DueDays": 30},
        {"Id": "4", "Name": "Net 15", "DueDays": 15},
    ]
    company.rows["Customer"] = [
        {
            "Id": "58",
            "SyncToken": "0",
            "DisplayName": "Acme Corp",
            "CompanyName": "Acme Corporation",
            "PrimaryEmailAddr": {"Address": "ap@acme.example"},
            "SalesTermRef": {"value": "3", "name": "Net 30"},
            "Notes": "Invoice code: AC",
        }
    ]
    company.rows["Vendor"] = [
        {
            "Id": "7",
            "SyncToken": "0",
            "DisplayName": "Priya Shah",
            "PrimaryEmailAddr": {"Address": "priya@example.com"},
            "TermRef": {"value": "4", "name": "Net 15"},
        }
    ]
    company.rows["Item"] = [
        {
            "Id": "39",
            "Name": "Acme Corp",
            "FullyQualifiedName": "Acme Corp",
            "Type": "Category",
            "Active": True,
        },
        {
            "Id": "40",
            "Name": "Priya Shah",
            "FullyQualifiedName": "Acme Corp:Priya Shah",
            "Type": "Service",
            "Active": True,
            "SubItem": True,
            "ParentRef": {"value": "39", "name": "Acme Corp"},
            "UnitPrice": 140.0,
            "PurchaseCost": 100.0,
            "PrefVendorRef": {"value": "7", "name": "Priya Shah"},
            "PurchaseDesc": "Start: 2026-08-01",
            "IncomeAccountRef": ACCOUNTS[0],
            "ExpenseAccountRef": ACCOUNTS[1],
        },
    ]
    return company


class TestWhatIsMadeIsReadBackAsToday:
    """The proof that matters: a setup made through the agent is read by the
    same code that bills every engagement today -- the engagement listing, the
    customer and vendor records, the product's two rates -- and nothing about
    it is incomplete (decisions 38, 43, 52, 53)."""

    def _read_back(self, company: StubCompany) -> EngagementWorkbook:
        # A new adapter, so nothing cached while setting up can stand in for
        # what QuickBooks would really answer.
        return workbook_from_accounting(adapter(company))

    def test_a_new_client_and_a_consultant_through_a_firm(self) -> None:
        company = company_as_icon_has_it()
        adapter(company).set_up_engagement(plan())

        built = self._read_back(company)

        assert built.problems == []
        globex = next(row for row in built.engagements if row.client == "Globex")
        assert globex.consultant == "Sam Okafor"
        assert globex.start_date == date(2026, 9, 1)
        assert globex.bill_rate == Money(15_000)  # the product's sales price
        assert globex.pay_rate == Money(11_000)  # the product's purchase cost
        client = next(c for c in built.clients if c.name == "Globex")
        assert client.legal_name == "Globex Corporation"
        assert client.billing_emails == ("ap@globex.example",)
        assert client.payment_terms_days == 30
        assert client.invoice_code == "GX"
        sam = next(c for c in built.consultants if c.name == "Sam Okafor")
        assert sam.emails == ("sam@example.com",)  # his timesheets will be recognised
        assert sam.vendor_company == "Okafor Consulting LLC"  # who Icon pays
        assert sam.pay_timing_days == 15
        # Acme and Priya, set up by hand, read exactly as before.
        assert {(row.consultant, row.client) for row in built.engagements} == {
            ("Priya Shah", "Acme Corp"),
            ("Sam Okafor", "Globex"),
        }

    def test_the_product_prices_an_invoice_as_a_hand_made_one_does(self) -> None:
        company = company_as_icon_has_it()
        adapter(company).set_up_engagement(plan())

        rates = adapter(company).engagement_rates("Sam Okafor", ["Globex"])

        assert rates is not None
        product = company.created("item")[-1]
        assert rates.ref == company.rows["Item"][-1]["Id"]
        assert (rates.bill_rate_cents, rates.pay_rate_cents) == (15_000, 11_000)
        assert rates.payee == "Okafor Consulting LLC"
        assert rates.payee_ref == product["PrefVendorRef"]["value"]
        payee = adapter(company).payee(rates.payee_ref)
        assert payee is not None and payee.payment_terms_days == 15
        customer = adapter(company).customer("Globex")
        assert customer is not None and customer.payment_terms_days == 30

    def test_an_existing_consultant_at_a_new_client_keeps_one_vendor(self) -> None:
        """Priya starts at Globex too: a second product under a second
        category, paid to the vendor she already has (decision 36)."""
        company = company_as_icon_has_it()
        done = adapter(company).set_up_engagement(
            plan(consultant="Priya Shah", consultant_email="priya@example.com", firm="")
        )

        assert "vendor Priya Shah" in done.reused
        assert company.created("vendor") == []
        built = self._read_back(company)
        assert built.problems == []
        assert {row.client for row in built.engagements if row.consultant == "Priya Shah"} == {
            "Acme Corp",
            "Globex",
        }
        (priya,) = [c for c in built.consultants if c.name == "Priya Shah"]
        assert priya.emails == ("priya@example.com",)
        rates = adapter(company).engagement_rates("Priya Shah", ["Globex"])
        assert rates is not None
        assert (rates.bill_rate_cents, rates.pay_rate_cents, rates.payee_ref) == (
            15_000,
            11_000,
            "7",
        )
        acme = adapter(company).engagement_rates("Priya Shah", ["Acme Corp"])
        assert acme is not None and acme.bill_rate_cents == 14_000  # untouched

    def test_a_new_engagement_at_an_existing_client_uses_its_category(self) -> None:
        company = company_as_icon_has_it()
        done = adapter(company).set_up_engagement(
            plan(client="Acme Corp", new_client=False, client_email="", invoice_code="")
        )

        assert {"customer Acme Corp", "category Acme Corp"} <= set(done.reused)
        product = company.created("item")[-1]
        assert product["ParentRef"] == {"value": "39"}
        built = self._read_back(company)
        assert built.problems == []
        assert ("Sam Okafor", "Acme Corp") in {(r.consultant, r.client) for r in built.engagements}
