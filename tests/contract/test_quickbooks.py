"""QuickBooks Online against recorded responses, and the token store on disk.

Every case the roadmap names for PR 9: a create whose total QuickBooks disagrees
with (voided and reported), a rotated refresh token, and finding an invoice the
agent already created by its private note after a crash. No network, no
credentials, no real company.
"""

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from finance_ops_agent.adapters.quickbooks.client import (
    QuickBooksClient,
    QuickBooksFailed,
    QuickBooksReconnect,
)
from finance_ops_agent.adapters.quickbooks.online import (
    PRODUCT_FIELDS,
    QuickBooksOnline,
    item_id_from_note,
    private_note,
)
from finance_ops_agent.adapters.quickbooks.tokens import (
    NotConnected,
    Tokens,
    TokenStore,
)
from finance_ops_agent.cli.doctor import ExpectedProduct
from finance_ops_agent.domain.invoices import Invoice, build_invoice
from finance_ops_agent.domain.money import Money
from tests.contract.http_replay import Replay
from tests.scenarios.conftest import ScenarioEnv
from tests.unit.test_emails import worked_example_item

RECORDINGS = Path(__file__).parent.parent / "fixtures" / "qbo"
NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
TODAY = NOW.date()
REALM = "9130350000000"


def replay_from(*names: str) -> Replay:
    script: dict[str, list[dict[str, object]]] = {}
    for name in names:
        recorded = json.loads((RECORDINGS / f"{name}.json").read_text())
        for entry in recorded["exchanges"]:
            script.setdefault(entry["key"], []).append(entry["response"])
    return Replay(script)


def tokens(refresh: str = "first-refresh-token", age_days: int = 1) -> Tokens:
    return Tokens(
        realm_id=REALM,
        access_token="an-access-token",
        refresh_token=refresh,
        access_expires_at=NOW + timedelta(hours=1),
        refreshed_at=NOW - timedelta(days=age_days),
        environment="sandbox",
    )


def build(
    replay: Replay, tmp_path: Path, stored: Tokens | None = None
) -> tuple[QuickBooksOnline, QuickBooksClient, TokenStore]:
    store = TokenStore(tmp_path / "qbo_tokens.json")
    store.save(stored or tokens())
    client = QuickBooksClient(
        store,
        client_id="a-client-id",
        client_secret="a-client-secret",
        http=replay.client(),
        now=lambda: NOW,
        sleep=lambda _seconds: None,
    )
    return QuickBooksOnline(client, TODAY), client, store


def worked_example_invoice(number: str = "083126AC-PS") -> Invoice:
    return build_invoice(worked_example_item(), number, TODAY)


class TestTokenStore:
    def test_round_trip(self, tmp_path: Path) -> None:
        store = TokenStore(tmp_path / "qbo_tokens.json")
        store.save(tokens())
        assert store.load() == tokens()

    def test_the_file_is_readable_only_by_its_owner(self, tmp_path: Path) -> None:
        store = TokenStore(tmp_path / "qbo_tokens.json")
        store.save(tokens())
        assert store.path.stat().st_mode & 0o077 == 0  # nobody else, tokens are secrets

    def test_not_connected_says_what_to_run(self, tmp_path: Path) -> None:
        with pytest.raises(NotConnected, match="qbo-connect"):
            TokenStore(tmp_path / "missing.json").load()

    def test_a_stale_access_token_is_noticed_before_it_expires(self) -> None:
        assert not tokens().access_token_is_stale(NOW)
        assert tokens().access_token_is_stale(NOW + timedelta(minutes=56))

    @pytest.mark.parametrize(
        ("age_days", "expected"),
        [(1, None), (79, None), (80, "stops working in about 20"), (101, "has expired")],
    )
    def test_the_refresh_token_warning(self, age_days: int, expected: str | None) -> None:
        warning = tokens(age_days=age_days).refresh_token_warning(NOW)
        if expected is None:
            assert warning is None
        else:
            assert warning is not None and expected in warning


class TestRefresh:
    def test_the_rotated_refresh_token_is_stored_before_it_is_used(self, tmp_path: Path) -> None:
        replay = replay_from("refresh")
        _, client, store = build(replay, tmp_path)

        rotated = client.refresh()

        assert rotated.refresh_token == "ROTATED-refresh-token"
        assert rotated.access_token == "new-access-token"
        # On disk, not just in memory: a crash here must not lose the connection.
        assert store.load().refresh_token == "ROTATED-refresh-token"
        assert store.load().refreshed_at == NOW
        assert rotated.access_expires_at == NOW + timedelta(hours=1)

    def test_a_refresh_that_returns_no_new_token_keeps_the_old_one(self, tmp_path: Path) -> None:
        replay = Replay(
            {
                "POST https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer": [
                    {"status": 200, "json": {"access_token": "new", "expires_in": 3600}}
                ]
            }
        )
        _, client, store = build(replay, tmp_path)
        assert client.refresh().refresh_token == "first-refresh-token"
        assert store.load().refresh_token == "first-refresh-token"

    def test_a_refused_refresh_asks_kevin_to_reconnect(self, tmp_path: Path) -> None:
        replay = replay_from("refresh_refused")
        _, client, _ = build(replay, tmp_path)
        with pytest.raises(QuickBooksReconnect, match="qbo-connect"):
            client.refresh()

    def test_a_401_is_retried_once_after_refreshing(self, tmp_path: Path) -> None:
        replay = replay_from("expired_then_ok")
        _, client, store = build(replay, tmp_path)

        company = client.get(client.company_url(f"/companyinfo/{REALM}"))

        assert company["CompanyInfo"]["CompanyName"] == "Icon Technologies (sandbox)"
        assert store.load().refresh_token == "ROTATED-2"

    def test_an_expired_access_token_refreshes_before_the_call(self, tmp_path: Path) -> None:
        replay = replay_from("refresh", "balances")
        accounting, _, store = build(
            replay,
            tmp_path,
            stored=Tokens(
                realm_id=REALM,
                access_token="expired",
                refresh_token="first-refresh-token",
                access_expires_at=NOW - timedelta(minutes=1),
                refreshed_at=NOW - timedelta(days=1),
                environment="sandbox",
            ),
        )
        accounting.paid_status(["145"])
        assert store.load().access_token == "new-access-token"


class TestCreateInvoice:
    def test_a_matching_total_is_accepted_with_its_pdf(self, tmp_path: Path) -> None:
        replay = replay_from("create_ok")
        accounting, _, _ = build(replay, tmp_path)

        created = accounting.create_invoice(worked_example_invoice(), item_id=1)

        assert created.number == "083126AC-PS"
        assert created.external_id == "145"
        assert created.pdf.startswith(b"%PDF")

    def test_the_created_invoice_carries_the_item_id_and_never_lets_qbo_email(
        self, tmp_path: Path
    ) -> None:
        replay = replay_from("create_ok")
        accounting, _, _ = build(replay, tmp_path)
        accounting.create_invoice(worked_example_invoice(), item_id=1)

        body = next(entry for entry in replay.bodies if isinstance(entry, dict) and "Line" in entry)
        assert body["PrivateNote"] == "fops item 1"
        # Kevin's number, not QuickBooks' own counter.
        assert body["DocNumber"] == "083126AC-PS"
        # If QuickBooks also emailed the invoice, the client would get it twice.
        assert body["EmailStatus"] == "NotSet"
        assert body["CustomerRef"] == {"value": "58"}
        line = body["Line"][0]
        assert line["SalesItemLineDetail"]["Qty"] == 156.0
        assert line["SalesItemLineDetail"]["UnitPrice"] == 140.0
        assert line["Amount"] == 21840.0
        assert line["SalesItemLineDetail"]["TaxCodeRef"] == {"value": "NON"}

    def test_a_total_quickbooks_disagrees_with_is_voided_and_reported(self, tmp_path: Path) -> None:
        replay = replay_from("create_mismatch")
        accounting, _, _ = build(replay, tmp_path)

        with pytest.raises(QuickBooksFailed) as error:
            accounting.create_invoice(worked_example_invoice(), item_id=1)

        message = str(error.value)
        assert "$23,678.40" in message  # what QuickBooks said
        assert "$21,840.00" in message  # what the timesheet comes to
        assert "voided it and sent nothing to the client" in message
        # The void really happened, and with the invoice's own sync token.
        assert any("operation=void" in url for _, url in replay.calls)
        void_body = replay.bodies[-1]
        assert void_body == {"Id": "146", "SyncToken": "0"}

    def test_an_invoice_quickbooks_numbered_itself_is_voided_and_reported(
        self, tmp_path: Path
    ) -> None:
        """The failure path for Kevin's numbering: QuickBooks only honours
        DocNumber when custom transaction numbers are on, and an invoice under
        a number Kevin did not choose never reaches a client."""
        replay = replay_from("create_own_number")
        accounting, _, _ = build(replay, tmp_path)

        with pytest.raises(QuickBooksFailed) as error:
            accounting.create_invoice(worked_example_invoice(), item_id=1)

        message = str(error.value)
        assert "083126AC-PS" in message  # what was asked for
        assert "1042" in message  # what QuickBooks used instead
        assert "Custom transaction numbers" in message  # and what to turn on
        assert any("operation=void" in url for _, url in replay.calls)
        assert replay.bodies[-1] == {"Id": "145", "SyncToken": "0"}

    def test_intuits_trace_id_is_kept_and_repeated_back(self, tmp_path: Path) -> None:
        """Intuit's support team asks for intuit_tid first, so it is captured
        from every response and put into anything the agent reports."""
        replay = replay_from("create_mismatch")
        accounting, client, _ = build(replay, tmp_path)

        with pytest.raises(QuickBooksFailed) as error:
            accounting.create_invoice(worked_example_invoice(), item_id=1)

        assert client.last_intuit_tid == "1-68c9a0f1-2b7d4e8a9c1f3b5d"
        assert "QuickBooks reference 1-68c9a0f1-2b7d4e8a9c1f3b5d" in str(error.value)

    def test_a_failure_without_a_trace_id_reads_normally(self, tmp_path: Path) -> None:
        """Not every response carries one; the message must not trail an empty
        bracket when it does not."""
        replay = replay_from("missing_item")
        accounting, client, _ = build(replay, tmp_path)

        with pytest.raises(QuickBooksFailed) as error:
            accounting.create_invoice(worked_example_invoice(), item_id=1)

        assert client.last_intuit_tid == ""
        assert "QuickBooks reference" not in str(error.value)

    def test_a_missing_customer_says_what_to_fix(self, tmp_path: Path) -> None:
        """Neither the display name nor the company name matched."""
        replay = replay_from("missing_customer")
        accounting, _, _ = build(replay, tmp_path)
        with pytest.raises(QuickBooksFailed, match="no customer whose name or company"):
            accounting.create_invoice(worked_example_invoice(), item_id=1)

    def test_a_product_rate_that_has_drifted_is_voided_and_reported(self, tmp_path: Path) -> None:
        """The rate is billed from the product now, so the engagement list is
        what notices when the two stop agreeing. Nothing is sent on a rate the
        agent cannot vouch for: the invoice is voided and Kevin is told."""
        replay = replay_from("product_rate_drifted")
        accounting, _, _ = build(replay, tmp_path)

        with pytest.raises(QuickBooksFailed) as error:
            accounting.create_invoice(worked_example_invoice(), item_id=1)

        message = str(error.value)
        assert "$22,620.00" in message  # 156.00 hours at the product's rate
        assert "$21,840.00" in message  # what the engagement list comes to
        assert "voided it and sent nothing to the client" in message
        assert any("operation=void" in url for _, url in replay.calls)

    def test_a_consultant_with_no_product_says_what_to_fix(self, tmp_path: Path) -> None:
        """Each consultant is a product and the product carries the rate, so a
        missing one is never worked around: billing them under someone else's
        product would bill the wrong rate."""
        replay = replay_from("missing_item")
        accounting, _, _ = build(replay, tmp_path)
        with pytest.raises(QuickBooksFailed) as error:
            accounting.create_invoice(worked_example_invoice(), item_id=1)
        message = str(error.value)
        assert "no product for 'Priya Shah'" in message
        assert "Acme Corp:Priya Shah" in message  # every path it tried is named
        assert "never create products myself" in message

    def test_the_line_is_priced_from_the_product_and_dated_by_the_period(
        self, tmp_path: Path
    ) -> None:
        """Decision 30: the rate comes off the consultant's product, the
        description is their name, and the service date is the period end,
        which is the column Kevin's template labels "period ending"."""
        replay = replay_from("create_ok")
        accounting, _, _ = build(replay, tmp_path)
        accounting.create_invoice(worked_example_invoice(), item_id=1)

        body = next(entry for entry in replay.bodies if isinstance(entry, dict) and "Line" in entry)
        line = body["Line"][0]
        assert line["Description"] == "Priya Shah"
        assert line["SalesItemLineDetail"]["ItemRef"] == {"value": "12"}  # Acme Corp:Priya Shah
        assert line["SalesItemLineDetail"]["ServiceDate"] == "2026-08-31"
        assert line["SalesItemLineDetail"]["UnitPrice"] == 140.0  # off the product


class TestFindingTheCustomer:
    """QuickBooks fills a customer's display name from whoever was typed in
    first, so an agency is often filed under a person with the organisation in
    the company field (decision 32)."""

    BASE = f"https://sandbox-quickbooks.api.intuit.com/v3/company/{REALM}"

    def _replay(self, by_company: dict[str, object]) -> Replay:
        from urllib.parse import quote

        name = "Virginia Information Technology Agency"
        return Replay(
            {
                f"GET {self.BASE}/query?query="
                + quote(f"SELECT Id, DisplayName FROM Customer WHERE DisplayName = '{name}'"): [
                    {"status": 200, "json": {"QueryResponse": {}}}
                ],
                f"GET {self.BASE}/query?query="
                + quote(
                    "SELECT Id, DisplayName, CompanyName FROM Customer"
                    f" WHERE CompanyName = '{name}'"
                ): [{"status": 200, "json": by_company}],
            }
        )

    def test_the_company_name_is_tried_when_the_display_name_is_a_person(
        self, tmp_path: Path
    ) -> None:
        replay = self._replay(
            {
                "QueryResponse": {
                    "Customer": [
                        {
                            "Id": "77",
                            "DisplayName": "Dana Whitfield",
                            "CompanyName": "Virginia Information Technology Agency",
                        }
                    ]
                }
            }
        )
        accounting, _, _ = build(replay, tmp_path)

        assert accounting.customer_ref("Virginia Information Technology Agency") == "77"

    def test_it_is_looked_up_once_and_remembered(self, tmp_path: Path) -> None:
        replay = self._replay(
            {"QueryResponse": {"Customer": [{"Id": "77", "DisplayName": "Dana Whitfield"}]}}
        )
        accounting, _, _ = build(replay, tmp_path)

        accounting.customer_ref("Virginia Information Technology Agency")
        accounting.customer_ref("Virginia Information Technology Agency")
        assert len(replay.calls) == 2  # the two from the first lookup, none after

    def test_a_company_name_shared_by_two_customers_is_refused(self, tmp_path: Path) -> None:
        """Display name is unique in QuickBooks and company name is not, so
        this one has to be a question rather than a guess."""
        replay = self._replay(
            {
                "QueryResponse": {
                    "Customer": [
                        {"Id": "77", "DisplayName": "Dana Whitfield"},
                        {"Id": "78", "DisplayName": "VITA — Accounts Payable"},
                    ]
                }
            }
        )
        accounting, _, _ = build(replay, tmp_path)

        with pytest.raises(QuickBooksFailed) as error:
            accounting.customer_ref("Virginia Information Technology Agency")
        message = str(error.value)
        assert "more than one customer" in message
        assert "Dana Whitfield" in message and "VITA — Accounts Payable" in message
        assert "will not guess" in message


class TestTheDoctorCheck:
    """A check that says only "missing" is a check that cannot be acted on:
    spelled differently, two of them, and connected to the wrong company all
    look the same once the reason is thrown away."""

    def test_it_repeats_the_reason_and_names_the_company(self, tmp_path: Path) -> None:
        from finance_ops_agent.cli.doctor import CheckResult, check_quickbooks_customers

        replay = replay_from("missing_customer")
        accounting, _, _ = build(replay, tmp_path)

        check = check_quickbooks_customers(accounting, lambda: ["Acme Corporation"])

        assert check.result is CheckResult.FAIL
        assert f"the sandbox company {REALM}" in check.detail
        assert "no customer whose name or company" in check.detail
        assert "Acme Corporation" in check.detail

    def test_a_pass_says_which_company_it_looked_in(self, tmp_path: Path) -> None:
        from finance_ops_agent.cli.doctor import CheckResult, check_quickbooks_customers

        replay = replay_from("create_ok")
        accounting, _, _ = build(replay, tmp_path)

        check = check_quickbooks_customers(accounting, lambda: ["Acme Corporation"])

        assert check.result is CheckResult.PASS
        assert f"the sandbox company {REALM}" in check.detail


class TestFindingTheProduct:
    """A product is an engagement, not a person: a consultant at two clients
    has two rates and one product cannot hold both. The category gives that a
    home, and QuickBooks maintains the path (decision 36)."""

    BASE = f"https://sandbox-quickbooks.api.intuit.com/v3/company/{REALM}"

    def _replay(self, answers: dict[str, object]) -> Replay:
        from urllib.parse import quote

        def key(statement: str) -> str:
            return f"GET {self.BASE}/query?query=" + quote(statement)

        empty: dict[str, object] = {"QueryResponse": {}}
        script: dict[str, list[dict[str, object]]] = {}
        for where, answer in (
            ("FullyQualifiedName = 'MasTec:Sridhar Doraiswamy'", answers.get("by_path", empty)),
            (
                "FullyQualifiedName = 'MasTec Inc:Sridhar Doraiswamy'",
                answers.get("by_legal", empty),
            ),
            ("Name = 'Sridhar Doraiswamy'", answers.get("by_name", empty)),
        ):
            statement = f"SELECT {PRODUCT_FIELDS} FROM Item WHERE " + where
            script[key(statement)] = [{"status": 200, "json": answer}]
        return Replay(script)

    def _item(self, item_id: str, path: str, rate: float = 140.0) -> dict[str, object]:
        return {
            "QueryResponse": {
                "Item": [
                    {
                        "Id": item_id,
                        "Name": "Sridhar Doraiswamy",
                        "UnitPrice": rate,
                        "FullyQualifiedName": path,
                    }
                ]
            }
        }

    def test_the_category_path_is_what_it_looks_for(self, tmp_path: Path) -> None:
        replay = self._replay({"by_path": self._item("21", "MasTec:Sridhar Doraiswamy")})
        accounting, _, _ = build(replay, tmp_path)

        product = accounting.product_for("Sridhar Doraiswamy", ["MasTec", "MasTec Inc"])

        assert product.ref == "21"
        assert product.unit_price_cents == 14_000
        assert len(replay.calls) == 1  # the first path answered; nothing else was asked

    def test_the_next_name_is_tried_when_the_first_finds_nothing(self, tmp_path: Path) -> None:
        """Kevin may have named the category for the client as QuickBooks knows
        it rather than as the engagement list does."""
        replay = self._replay({"by_legal": self._item("22", "MasTec Inc:Sridhar Doraiswamy")})
        accounting, _, _ = build(replay, tmp_path)

        product = accounting.product_for("Sridhar Doraiswamy", ["MasTec", "MasTec Inc"])

        assert product.ref == "22"

    def test_a_product_with_no_category_yet_still_works(self, tmp_path: Path) -> None:
        """The bridge while the categories are being filled in: one product of
        that name and no ambiguity about which rate it carries."""
        replay = self._replay({"by_name": self._item("23", "Sridhar Doraiswamy")})
        accounting, _, _ = build(replay, tmp_path)

        product = accounting.product_for("Sridhar Doraiswamy", ["MasTec", "MasTec Inc"])

        assert product.ref == "23"

    def test_two_products_of_that_name_and_no_category_is_refused(self, tmp_path: Path) -> None:
        """This is the case that would have billed the wrong rate."""
        replay = self._replay(
            {
                "by_name": {
                    "QueryResponse": {
                        "Item": [
                            {
                                "Id": "24",
                                "Name": "Sridhar Doraiswamy",
                                "UnitPrice": 140.0,
                                "FullyQualifiedName": "Northwind:Sridhar Doraiswamy",
                            },
                            {
                                "Id": "25",
                                "Name": "Sridhar Doraiswamy",
                                "UnitPrice": 120.0,
                                "FullyQualifiedName": "Harbour Point:Sridhar Doraiswamy",
                            },
                        ]
                    }
                }
            }
        )
        accounting, _, _ = build(replay, tmp_path)

        with pytest.raises(QuickBooksFailed) as error:
            accounting.product_for("Sridhar Doraiswamy", ["MasTec", "MasTec Inc"])

        message = str(error.value)
        assert "more than one product" in message
        assert "Northwind:Sridhar Doraiswamy" in message
        assert "Harbour Point:Sridhar Doraiswamy" in message
        assert "MasTec:Sridhar Doraiswamy" in message  # what it was looking for

    def test_it_asks_for_the_entity_not_a_field_list(self, tmp_path: Path) -> None:
        """QuickBooks refuses PrefVendorRef in a SELECT -- "Property
        PrefVendorRef not found for Entity Item" -- because references come
        back with the entity or not at all."""
        replay = self._replay({"by_path": self._item("21", "MasTec:Sridhar Doraiswamy")})
        accounting, _, _ = build(replay, tmp_path)

        accounting.product_for("Sridhar Doraiswamy", ["MasTec"])

        [(_, url)] = replay.calls
        assert "SELECT%20%2A%20FROM%20Item" in url
        assert "PrefVendorRef" not in url

    def test_the_purchase_side_is_read_too(self, tmp_path: Path) -> None:
        """What Icon pays and who it pays, read but not used yet (decision 37),
        so QuickBooks and the engagement list can be compared."""
        answer: dict[str, object] = {
            "QueryResponse": {
                "Item": [
                    {
                        "Id": "21",
                        "Name": "Sridhar Doraiswamy",
                        "UnitPrice": 140.0,
                        "FullyQualifiedName": "MasTec:Sridhar Doraiswamy",
                        "PurchaseCost": 100.0,
                        "PrefVendorRef": {"value": "9", "name": "Blue Peak Consulting LLC"},
                    }
                ]
            }
        }
        replay = self._replay({"by_path": answer})
        accounting, _, _ = build(replay, tmp_path)

        product = accounting.product_for("Sridhar Doraiswamy", ["MasTec"])

        assert product.purchase_cost_cents == 10_000
        assert product.vendor == "Blue Peak Consulting LLC"

    def test_a_product_with_nothing_on_its_purchase_side(self, tmp_path: Path) -> None:
        """Not filled in is not the same as nothing owed."""
        replay = self._replay({"by_path": self._item("21", "MasTec:Sridhar Doraiswamy")})
        accounting, _, _ = build(replay, tmp_path)

        product = accounting.product_for("Sridhar Doraiswamy", ["MasTec"])

        assert product.purchase_cost_cents is None
        assert product.vendor == ""

    def test_the_same_consultant_at_two_clients_gets_two_rates(self, tmp_path: Path) -> None:
        """The whole point: one product per consultant could only hold one."""
        from urllib.parse import quote

        def key(client: str) -> str:
            return f"GET {self.BASE}/query?query=" + quote(
                f"SELECT {PRODUCT_FIELDS} FROM Item"
                f" WHERE FullyQualifiedName = '{client}:Sridhar Doraiswamy'"
            )

        replay = Replay(
            {
                key("MasTec"): [
                    {"status": 200, "json": self._item("21", "MasTec:Sridhar Doraiswamy", 140.0)}
                ],
                key("iStream"): [
                    {"status": 200, "json": self._item("31", "iStream:Sridhar Doraiswamy", 120.0)}
                ],
            }
        )
        accounting, _, _ = build(replay, tmp_path)

        at_mastec = accounting.product_for("Sridhar Doraiswamy", ["MasTec"])
        at_istream = accounting.product_for("Sridhar Doraiswamy", ["iStream"])

        assert at_mastec.unit_price_cents == 14_000
        assert at_istream.unit_price_cents == 12_000


class TestThePayRatesCheck:
    """`fops doctor` compares the purchase side with the engagement list, so
    the two can be made to agree before anything moves across (decision 37)."""

    BASE = TestFindingTheProduct.BASE

    def _expected(
        self, pay_cents: int = 10_000, payee: str = "Blue Peak Consulting LLC"
    ) -> list["ExpectedProduct"]:
        from finance_ops_agent.cli.doctor import ExpectedProduct

        return [
            ExpectedProduct(
                consultant="Sridhar Doraiswamy",
                client="MasTec",
                clients=["MasTec"],
                bill_rate_cents=14_000,
                pay_rate_cents=pay_cents,
                payee=payee,
            )
        ]

    def _replay(self, purchase: dict[str, object]) -> Replay:
        from urllib.parse import quote

        item: dict[str, object] = {
            "Id": "21",
            "Name": "Sridhar Doraiswamy",
            "UnitPrice": 140.0,
            "FullyQualifiedName": "MasTec:Sridhar Doraiswamy",
        }
        item.update(purchase)
        statement = (
            f"SELECT {PRODUCT_FIELDS} FROM Item"
            " WHERE FullyQualifiedName = 'MasTec:Sridhar Doraiswamy'"
        )
        return Replay(
            {
                f"GET {self.BASE}/query?query=" + quote(statement): [
                    {"status": 200, "json": {"QueryResponse": {"Item": [item]}}}
                ]
            }
        )

    def test_agreement_passes(self, tmp_path: Path) -> None:
        from finance_ops_agent.cli.doctor import CheckResult, check_quickbooks_pay

        replay = self._replay(
            {
                "PurchaseCost": 100.0,
                "PrefVendorRef": {"value": "9", "name": "Blue Peak Consulting LLC"},
            }
        )
        accounting, _, _ = build(replay, tmp_path)

        check = check_quickbooks_pay(accounting, self._expected)

        assert check.result is CheckResult.PASS
        assert "agree with the engagement list" in check.detail

    def test_a_pay_rate_that_disagrees_is_named_with_both_figures(self, tmp_path: Path) -> None:
        from finance_ops_agent.cli.doctor import CheckResult, check_quickbooks_pay

        replay = self._replay({"PurchaseCost": 110.0})
        accounting, _, _ = build(replay, tmp_path)

        check = check_quickbooks_pay(accounting, self._expected)

        assert check.result is CheckResult.FAIL
        assert "$110.00" in check.detail  # what QuickBooks holds
        assert "$100.00" in check.detail  # what the engagement list says
        assert "no payment instruction is wrong today" in check.detail

    def test_a_different_payee_is_named(self, tmp_path: Path) -> None:
        from finance_ops_agent.cli.doctor import CheckResult, check_quickbooks_pay

        replay = self._replay(
            {"PurchaseCost": 100.0, "PrefVendorRef": {"value": "9", "name": "Someone Else Ltd"}}
        )
        accounting, _, _ = build(replay, tmp_path)

        check = check_quickbooks_pay(accounting, self._expected)

        assert check.result is CheckResult.FAIL
        assert "Someone Else Ltd" in check.detail
        assert "Blue Peak Consulting LLC" in check.detail

    def test_nothing_filled_in_is_not_a_disagreement(self, tmp_path: Path) -> None:
        """A product whose purchase side is empty has not been filled in; it
        does not disagree with anything."""
        from finance_ops_agent.cli.doctor import CheckResult, check_quickbooks_pay

        replay = self._replay({})
        accounting, _, _ = build(replay, tmp_path)

        check = check_quickbooks_pay(accounting, self._expected)

        assert check.result is CheckResult.PASS
        assert "nothing to compare yet" in check.detail


class TestCancelling:
    """Renamed first, then voided: QuickBooks will not give a number back while
    any invoice still holds it, and a voided one is not something to count on
    being editable (decision 34)."""

    BASE = f"https://sandbox-quickbooks.api.intuit.com/v3/company/{REALM}"

    def test_the_invoice_is_renamed_before_it_is_voided(self, tmp_path: Path) -> None:
        replay = replay_from("void_after_renaming")
        accounting, _, _ = build(replay, tmp_path)

        accounting.cancel_invoice("145", renamed_to="083126AC-PS-VOID")

        methods = [f"{method} {url.split('/company/')[1]}" for method, url in replay.calls]
        assert methods == [
            f"GET {REALM}/invoice/145",
            f"POST {REALM}/invoice",  # the rename, while it is still live
            f"POST {REALM}/invoice?operation=void",
        ]
        rename, void = replay.bodies[0], replay.bodies[1]
        assert rename == {
            "Id": "145",
            "SyncToken": "0",
            "sparse": True,
            "DocNumber": "083126AC-PS-VOID",
        }
        # Voided with the sync token the rename handed back, not the stale one.
        assert void == {"Id": "145", "SyncToken": "1"}

    def test_a_rename_that_fails_does_not_stop_the_void(self, tmp_path: Path) -> None:
        """An invoice Kevin cancelled must not survive because its number could
        not be changed. The number stays spent, which is untidy, not wrong."""
        replay = Replay(
            {
                f"GET {TestCancelling.BASE}/invoice/145": [
                    {"status": 200, "json": {"Invoice": {"Id": "145", "SyncToken": "0"}}}
                ],
                f"POST {TestCancelling.BASE}/invoice": [
                    {"status": 400, "json": {"Fault": {"Error": [{"Message": "no"}]}}}
                ],
                f"POST {TestCancelling.BASE}/invoice?operation=void": [
                    {"status": 200, "json": {"Invoice": {"Id": "145"}}}
                ],
            }
        )
        accounting, _, _ = build(replay, tmp_path)

        accounting.cancel_invoice("145", renamed_to="083126AC-PS-VOID")

        assert any("operation=void" in url for _, url in replay.calls)
        assert replay.bodies[-1] == {"Id": "145", "SyncToken": "0"}

    def test_a_voided_invoice_is_not_mistaken_for_a_live_one(self, tmp_path: Path) -> None:
        """After a crash the agent asks QuickBooks whether it already made this
        item's invoice. One it cancelled is not an answer."""
        from urllib.parse import quote

        recent = quote(
            "SELECT Id, DocNumber, TotalAmt, PrivateNote, Balance FROM Invoice"
            " WHERE TxnDate >= '2026-09-01' ORDERBY TxnDate DESC MAXRESULTS 100"
        )
        replay = Replay(
            {
                f"GET {TestCancelling.BASE}/query?query={recent}": [
                    {
                        "status": 200,
                        "json": {
                            "QueryResponse": {
                                "Invoice": [
                                    {
                                        "Id": "145",
                                        "DocNumber": "083126AC-PS-VOID",
                                        "TotalAmt": 0,
                                        "PrivateNote": "fops item 1",
                                    }
                                ]
                            }
                        },
                    }
                ]
            }
        )
        accounting, _, _ = build(replay, tmp_path)

        assert accounting.find_invoice(item_id=1) is None


class TestFindAfterACrash:
    """The crash question: did I already create this invoice?"""

    def test_an_invoice_this_agent_created_is_recognised_by_its_private_note(
        self, tmp_path: Path
    ) -> None:
        replay = replay_from("find_existing")
        accounting, _, _ = build(replay, tmp_path)

        found = accounting.find_invoice(item_id=1)

        assert found is not None
        assert found.external_id == "145"
        assert found.number == "1042"

    def test_creating_after_a_crash_returns_the_existing_one_instead_of_a_second(
        self, tmp_path: Path
    ) -> None:
        replay = replay_from("find_existing")
        accounting, _, _ = build(replay, tmp_path)

        created = accounting.create_invoice(worked_example_invoice(), item_id=1)

        assert created.external_id == "145"
        # No create call was made at all.
        assert not any(
            url.endswith("/invoice") and method == "POST" for method, url in replay.calls
        )

    def test_someone_elses_invoice_is_not_mistaken_for_ours(self, tmp_path: Path) -> None:
        replay = replay_from("find_existing")
        accounting, _, _ = build(replay, tmp_path)
        # Item 2 has no invoice; the bookkeeper's hand-made one must not match.
        assert accounting.find_invoice(item_id=2) is None

    @pytest.mark.parametrize(
        ("note", "expected"),
        [
            ("fops item 7", 7),
            ("fops item 7 (replaces invoice ICON-2026-1001)", 7),
            ("made by hand by the bookkeeper", None),
            ("fops item", None),
            ("fops item seven", None),
            ("", None),
        ],
    )
    def test_reading_the_item_id_back_out_of_a_note(self, note: str, expected: int | None) -> None:
        assert item_id_from_note(note) == expected

    def test_a_replacement_note_says_what_it_replaces(self) -> None:
        assert private_note(7, "ICON-2026-1001") == (
            "fops item 7 (replaces invoice ICON-2026-1001)"
        )


class TestPaidAndVoid:
    def test_balance_zero_is_paid_and_a_partial_payment_is_not(self, tmp_path: Path) -> None:
        replay = replay_from("balances")
        accounting, _, _ = build(replay, tmp_path)

        paid = accounting.paid_status(["145", "146", "147"])

        assert paid == {"145": True, "146": False, "147": False}

    def test_the_remaining_balance_is_reported_in_whole_cents(self, tmp_path: Path) -> None:
        replay = replay_from("balances")
        accounting, _, _ = build(replay, tmp_path)
        assert accounting.remaining_balance("146") == Money(1_000_000)

    def test_voiding_reads_the_current_sync_token_first(self, tmp_path: Path) -> None:
        replay = replay_from("void")
        accounting, _, _ = build(replay, tmp_path)

        accounting.cancel_invoice("145")

        assert replay.bodies[-1] == {"Id": "145", "SyncToken": "3"}


class TestCrashDuringAWholeRun:
    """The roadmap's requirement, through the run rather than the adapter alone:
    after a crash the agent finds the invoice it already created and does not
    create another."""

    def test_a_run_that_crashes_after_creating_does_not_create_a_second_invoice(
        self, tmp_path: Path
    ) -> None:
        from dataclasses import replace as dc_replace

        from finance_ops_agent.adapters.fakes.sender import FakeSender, SimulatedCrash
        from finance_ops_agent.application.run import run_once

        # Run 1: nothing there yet, so it creates - then dies before the
        # billing email. Run 2: the private note is found, so it must not create.
        replay = replay_from("create_ok", "find_existing")
        accounting, _, _ = build(replay, tmp_path)
        env = _auto_env(tmp_path, accounting)

        crashing = FakeSender(tmp_path / "outbox", crash_before_send=True)
        with pytest.raises(SimulatedCrash):
            run_once(dc_replace(env.deps(), sender=crashing))

        item = env.the_item()
        created_first = env.store.invoices_for_item(item.id)
        assert len(created_first) == 1

        # Restart: the invoice is found by its private note, not created again.
        env.run()

        assert len(env.store.invoices_for_item(item.id)) == 1
        creates = [
            url for method, url in replay.calls if method == "POST" and url.endswith("/invoice")
        ]
        assert len(creates) == 1, "the invoice was created exactly once"


def _auto_env(tmp_path: Path, accounting: QuickBooksOnline) -> "ScenarioEnv":
    """A scenario environment in automatic mode, using the real QBO adapter."""
    from finance_ops_agent.adapters.fakes.mailbox import FakeMailbox
    from finance_ops_agent.adapters.fakes.sender import FakeSender
    from finance_ops_agent.adapters.fakes.store import FakeStore
    from finance_ops_agent.application.context import Mode
    from tests.scenarios.conftest import (
        PRIYA,
        default_workbook,
        engagement_row,
        reading,
    )

    mailbox_dir = tmp_path / "mailbox"
    mailbox_dir.mkdir(exist_ok=True)
    workbook = default_workbook()
    workbook.engagements[0] = engagement_row(2, **{"Send automatically": "yes"})
    env = ScenarioEnv(
        mailbox_dir=mailbox_dir,
        mailbox=FakeMailbox(mailbox_dir),
        store=FakeStore(),
        readings={},
        workbook=workbook,
        sender=FakeSender(tmp_path / "outbox"),
        accounting=accounting,  # type: ignore[arg-type]
        today=TODAY,
        mode=Mode.AUTO,
    )
    env.add_email(
        PRIYA,
        scripted_reading=reading(date(2026, 8, 1), date(2026, 8, 31)),
    )
    return env
