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
        row = {**json, "Id": str(self._next), "SyncToken": "0"}
        if entity == "Item" and "ParentRef" in json:
            parent = next(r for r in self.rows["Item"] if r["Id"] == json["ParentRef"]["value"])
            row["FullyQualifiedName"] = f"{parent['Name']}:{json['Name']}"
        self.rows.setdefault(entity, []).append(row)
        return {entity: row}

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
