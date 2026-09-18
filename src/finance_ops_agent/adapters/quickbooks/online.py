"""QuickBooks Online as the AccountingSystem (FOPS_ACCOUNTING=quickbooks).

Three rules from docs/integrations/quickbooks-online.md carry the weight:

- The total QuickBooks returns must equal the agent's own amount to the cent.
  If it does not, the invoice is voided immediately and QUICKBOOKS_FAILED is
  raised: an invoice the agent cannot vouch for never reaches a client.
- The item id goes in PrivateNote, which is how the agent recognises an
  invoice it already created after a crash, instead of creating a second one.
- EmailStatus is NotSet: the agent sends the billing email itself, so
  QuickBooks must never also send one or the client would get two.
- A client is found by its display name in QuickBooks or, failing that, by its
  company name: an agency is often filed under a person's name with the
  organisation in the company field (decision 32).
- Each consultant is a product in QuickBooks and the product carries the rate,
  so the line is priced from QuickBooks, not from the engagement list
  (decision 30). The total that comes back is still checked against the
  engagement list, which is what catches the two drifting apart.
- DocNumber is Kevin's number, worked out by the application (decision 27).
  QuickBooks only honours it when "Custom transaction numbers" is on in the
  company settings, so the number that comes back is checked against the one
  that was asked for, and an invoice QuickBooks numbered itself is voided.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from finance_ops_agent import logs
from finance_ops_agent.adapters.quickbooks.client import (
    QuickBooksClient,
    QuickBooksFailed,
    with_trace,
)
from finance_ops_agent.domain.invoice_numbers import is_voided_number
from finance_ops_agent.domain.invoices import Invoice
from finance_ops_agent.domain.money import Money, invoice_amount
from finance_ops_agent.ports.accounting import CreatedInvoice, EngagementRates

PRIVATE_NOTE_PREFIX = "fops item"
# The whole entity, not a field list. QuickBooks' query language refuses
# `PrefVendorRef` in a SELECT ("Property PrefVendorRef not found for Entity
# Item"): references come back with the entity or not at all. Asking for the
# entity also means this does not have to be kept in step with which fields
# the query language happens to accept.
PRODUCT_FIELDS = "*"


def private_note(item_id: int, replaces: str | None = None) -> str:
    note = f"{PRIVATE_NOTE_PREFIX} {item_id}"
    if replaces:
        note += f" (replaces invoice {replaces})"
    return note


def item_id_from_note(note: str) -> int | None:
    if not note.startswith(PRIVATE_NOTE_PREFIX):
        return None
    rest = note[len(PRIVATE_NOTE_PREFIX) :].strip().split()
    if not rest:
        return None
    try:
        return int(rest[0])
    except ValueError:
        return None


def _escape(value: str) -> str:
    """QuickBooks' query language wants an apostrophe backslash-escaped."""
    return value.replace("'", "\\'")


def _cents(amount: Any) -> int:
    """QuickBooks sends money as a JSON number; compare in whole cents only."""
    return round(float(amount) * 100)


def client_names(invoice: Invoice) -> list[str]:
    """The names this client might be filed under in QuickBooks, in the order
    worth trying: the engagement list's own short name, then the QuickBooks
    customer name, then the legal name."""
    seen: list[str] = []
    for name in (invoice.client_name, invoice.quickbooks_customer, invoice.client_legal_name):
        if name and name not in seen:
            seen.append(name)
    return seen


@dataclass(frozen=True)
class Product:
    """One engagement's product in QuickBooks: the two sides of its rate.

    The sales side (`unit_price_cents`) is what the client is billed, and is
    what the agent invoices from (decision 30). The purchase side
    (`purchase_cost_cents`) is what Icon pays for those hours, and the
    preferred vendor is who it pays -- both read, neither used yet, so that
    what QuickBooks holds can be compared with the engagement list before
    anything depends on it (decision 37). `None` means QuickBooks has nothing
    there, which is different from nothing being owed.
    """

    ref: str
    name: str
    unit_price_cents: int
    purchase_cost_cents: int | None = None
    vendor: str = ""


class QuickBooksOnline:
    def __init__(self, client: QuickBooksClient, today: date) -> None:
        self._client = client
        self._today = today  # from the Clock port, never the system clock
        self._customers: dict[str, str] = {}
        self._products: dict[tuple[str, tuple[str, ...]], Product] = {}

    @property
    def company(self) -> str:
        """Which company this is actually connected to, in words.

        Worth saying out loud whenever a lookup fails: a name missing from the
        sandbox while it sits in the real company looks exactly like a name
        that is spelled wrong.
        """
        tokens = self._client.tokens
        return f"the {tokens.environment} company {tokens.realm_id}"

    # --- lookups, cached for the run ---

    def customer_ref(self, quickbooks_customer: str) -> str:
        """The client's customer id, by display name or by company name.

        QuickBooks fills a customer's display name from whoever was typed in
        first, which for an agency is often a person rather than the
        organisation, while the organisation sits in the company name field. So
        the name on the engagement list is tried against both (decision 32).
        Display name is unique in QuickBooks and company name is not, so a
        company name matching more than one customer is refused rather than
        guessed between.
        """
        if quickbooks_customer in self._customers:
            return self._customers[quickbooks_customer]
        escaped = _escape(quickbooks_customer)
        matched_on = "DisplayName"
        rows = self._client.query(
            f"SELECT Id, DisplayName FROM Customer WHERE DisplayName = '{escaped}'"
        )
        if not rows:
            matched_on = "CompanyName"
            rows = self._client.query(
                f"SELECT Id, DisplayName, CompanyName FROM Customer WHERE CompanyName = '{escaped}'"
            )
        if len(rows) > 1:
            names = ", ".join(sorted(str(row.get("DisplayName") or row["Id"]) for row in rows))
            logs.log("quickbooks company name is ambiguous", customer=quickbooks_customer)
            raise QuickBooksFailed(
                f"QuickBooks has more than one customer whose company is"
                f" {quickbooks_customer!r}: {names}. I will not guess which one to"
                ' invoice. Put the one you mean in the "QuickBooks customer" column'
                " of the engagement list, spelled as QuickBooks shows it in the list"
                " of customers."
            )
        if not rows:
            logs.log("quickbooks customer not found", customer=quickbooks_customer)
            raise QuickBooksFailed(
                f"QuickBooks has no customer whose name or company is"
                f" {quickbooks_customer!r}. Add it in QuickBooks, or fix the"
                ' "QuickBooks customer" column in the engagement list. I never'
                " create customers myself."
            )
        reference = str(rows[0]["Id"])
        logs.log(
            "quickbooks customer found",
            customer=quickbooks_customer,
            quickbooks_id=reference,
            matched_on=matched_on,
        )
        self._customers[quickbooks_customer] = reference
        return reference

    def product_for(self, consultant: str, clients: Sequence[str] = ()) -> Product:
        """The product for this consultant at this client, and its rate.

        An engagement, not a person: a consultant working at two clients has
        two rates, and one product cannot hold both. QuickBooks categories give
        that a home -- the product sits under a category named for the client,
        and its `FullyQualifiedName` is `MasTec:Sridhar Doraiswamy`, which
        QuickBooks maintains itself and lets us filter on (decision 36).

        `clients` is the names that client might be filed under, tried in turn:
        the engagement list's short name first, then the QuickBooks customer
        name, then the legal name. Each is an exact match; the first that finds
        a product wins, and the log says which.

        A product not yet under a category is still used, but only when exactly
        one product has that name -- a bridge while the categories are being
        filled in. Two products sharing a name and no category to tell them
        apart is refused, never guessed between: that is the wrong rate.
        """
        key = (consultant, tuple(clients))
        if key in self._products:
            return self._products[key]
        tried: list[str] = []
        for client in clients:
            if not client:
                continue
            path = f"{client}:{consultant}"
            tried.append(path)
            rows = self._client.query(
                f"SELECT {PRODUCT_FIELDS} FROM Item WHERE FullyQualifiedName = '{_escape(path)}'"
            )
            if rows:
                return self._remember(key, consultant, rows[0], path)

        # No category yet: allow it while exactly one product answers to the name.
        rows = self._client.query(
            f"SELECT {PRODUCT_FIELDS} FROM Item WHERE Name = '{_escape(consultant)}'"
        )
        if len(rows) > 1:
            names = ", ".join(
                sorted(str(row.get("FullyQualifiedName") or row["Id"]) for row in rows)
            )
            logs.log("quickbooks product is ambiguous", consultant=consultant)
            raise QuickBooksFailed(
                f"QuickBooks has more than one product called {consultant!r} and none of"
                f" them is under a category I recognise: {names}. I looked for"
                f" {' or '.join(tried) or 'a category'}. Put the product under a category"
                " named for the client, so I can tell which rate to bill."
            )
        if not rows:
            logs.log("quickbooks product not found", consultant=consultant)
            raise QuickBooksFailed(
                f"QuickBooks has no product for {consultant!r}. I looked for"
                f" {' or '.join(tried) or 'a category'}, and for a product called"
                f" {consultant!r} on its own. Every engagement needs a product, under a"
                " category named for the client, with the rate on it. I never create"
                " products myself."
            )
        return self._remember(key, consultant, rows[0], "")

    def _remember(
        self, key: tuple[str, tuple[str, ...]], consultant: str, row: dict[str, Any], path: str
    ) -> Product:
        if row.get("UnitPrice") is None:
            logs.log("quickbooks product has no rate", consultant=consultant)
            raise QuickBooksFailed(
                f"The QuickBooks product for {consultant!r} has no rate on it, and the"
                " rate on the product is what I bill. Put their hourly rate on the"
                " product in QuickBooks."
            )
        cost = row.get("PurchaseCost")
        vendor = row.get("PrefVendorRef") or {}
        product = Product(
            ref=str(row["Id"]),
            name=str(row.get("FullyQualifiedName") or row.get("Name") or consultant),
            unit_price_cents=_cents(row["UnitPrice"]),
            purchase_cost_cents=None if cost is None else _cents(cost),
            vendor=str(vendor.get("name") or "") if isinstance(vendor, dict) else "",
        )
        logs.log(
            "quickbooks product found",
            consultant=consultant,
            quickbooks_id=product.ref,
            matched_on=path or "the name alone, with no category",
        )
        self._products[key] = product
        return product

    # --- the AccountingSystem port ---

    def create_invoice(self, invoice: Invoice, item_id: int) -> CreatedInvoice:
        existing = self.find_invoice(item_id)
        if existing is not None:
            return existing  # a crash left one behind; never create a second
        body = self._invoice_body(invoice, item_id)
        logs.log(
            "creating a quickbooks invoice",
            item_id=item_id,
            number=invoice.number,
            consultant=invoice.consultant,
            client=invoice.client_legal_name,
        )
        created = self._client.post(self.company_url("/invoice"), json=body)
        raw = created.get("Invoice", created)
        quickbooks_id = str(raw["Id"])
        total_cents = _cents(raw.get("TotalAmt", 0))
        given_number = str(raw.get("DocNumber") or "")

        if given_number != invoice.number:
            logs.log(
                "quickbooks numbered the invoice itself",
                item_id=item_id,
                asked_for=invoice.number,
                given=given_number,
            )
            # QuickBooks numbered it itself, which means custom transaction
            # numbers are off. Kevin's numbering is how he and the client find
            # an invoice again, so this is not something to paper over.
            self._void(quickbooks_id, str(raw.get("SyncToken", "0")))
            raise QuickBooksFailed(
                with_trace(
                    f"I asked QuickBooks to number this invoice {invoice.number} and it"
                    f" used {given_number or '(nothing)'} instead. Turn on Settings ->"
                    " Account and settings -> Sales -> Custom transaction numbers, and"
                    " I'll number invoices the way you do. I voided it and sent nothing"
                    " to the client.",
                    self._client.last_intuit_tid,
                )
            )
        if total_cents != invoice.total.cents:
            # Void first, then report: an amount the agent cannot vouch for must
            # not survive, and it must never reach a client. Since the rate now
            # comes off the product, this is also what catches the product's
            # rate and the engagement list's having drifted apart.
            logs.log(
                "quickbooks total disagrees with the engagement list",
                item_id=item_id,
                number=invoice.number,
                consultant=invoice.consultant,
            )
            sync_token = str(raw.get("SyncToken", "0"))
            self._void(quickbooks_id, sync_token)
            raise QuickBooksFailed(
                with_trace(
                    f"QuickBooks totalled invoice {raw.get('DocNumber', quickbooks_id)} at"
                    f" ${Money(total_cents)}, but this timesheet comes to"
                    f" ${invoice.total}. I voided it and sent nothing to the client."
                    " This usually means the rate on the consultant's product in"
                    " QuickBooks and the rate on the engagement list have stopped"
                    " agreeing.",
                    self._client.last_intuit_tid,
                )
            )
        logs.log(
            "quickbooks invoice created",
            item_id=item_id,
            number=given_number,
            quickbooks_id=quickbooks_id,
        )
        return CreatedInvoice(
            number=given_number,
            external_id=quickbooks_id,
            pdf=self.invoice_pdf(quickbooks_id),
        )

    def engagement_rates(self, consultant: str, clients: Sequence[str]) -> EngagementRates | None:
        """Both sides of the engagement's product (decision 38)."""
        product = self.product_for(consultant, clients)
        return EngagementRates(
            bill_rate_cents=product.unit_price_cents,
            pay_rate_cents=product.purchase_cost_cents,
            payee=product.vendor,
        )

    def find_invoice(self, item_id: int) -> CreatedInvoice | None:
        """Recognise an invoice this agent already created, by its private note."""
        note = private_note(item_id)
        recent = (self._today - timedelta(days=2)).isoformat()
        rows = self._client.query(
            "SELECT Id, DocNumber, TotalAmt, PrivateNote, Balance FROM Invoice"
            f" WHERE TxnDate >= '{recent}' ORDERBY TxnDate DESC MAXRESULTS 100"
        )
        for row in rows:
            stored = str(row.get("PrivateNote") or "")
            if item_id_from_note(stored) != item_id_from_note(note):
                continue
            if is_voided_number(str(row.get("DocNumber") or "")):
                continue  # cancelled, and renamed to say so; not this item's invoice
            quickbooks_id = str(row["Id"])
            return CreatedInvoice(
                number=str(row.get("DocNumber") or quickbooks_id),
                external_id=quickbooks_id,
                pdf=self.invoice_pdf(quickbooks_id),
            )
        return None

    def cancel_invoice(self, external_id: str, renamed_to: str | None = None) -> None:
        """Rename it, then void it.

        In that order: QuickBooks will not give a number back while any
        invoice, voided or not, still holds it, and a voided invoice is not
        something to count on being editable. So the rename happens while the
        invoice is still an ordinary one (decision 34). A rename that fails is
        reported but does not stop the void -- an invoice Kevin cancelled must
        not survive because its number could not be changed.
        """
        raw = self._client.get(self.company_url(f"/invoice/{external_id}"))
        invoice = raw.get("Invoice", raw)
        sync_token = str(invoice.get("SyncToken", "0"))
        if renamed_to is not None and str(invoice.get("DocNumber") or "") != renamed_to:
            sync_token = self._rename(external_id, sync_token, renamed_to)
        self._void(external_id, sync_token)

    def _rename(self, quickbooks_id: str, sync_token: str, number: str) -> str:
        """Set DocNumber on an invoice, returning the sync token to void with."""
        logs.log("renaming a quickbooks invoice", quickbooks_id=quickbooks_id, number=number)
        try:
            raw = self._client.post(
                self.company_url("/invoice"),
                json={
                    "Id": quickbooks_id,
                    "SyncToken": sync_token,
                    "sparse": True,
                    "DocNumber": number,
                },
            )
        except QuickBooksFailed as error:
            # The number stays spent and the replacement takes the next one
            # along, which is untidy rather than wrong.
            logs.log("could not rename the invoice", quickbooks_id=quickbooks_id, said=str(error))
            return sync_token
        renamed = raw.get("Invoice", raw)
        return str(renamed.get("SyncToken", sync_token))

    def paid_status(self, external_ids: list[str]) -> dict[str, bool]:
        """Balance == 0 means paid. Partial payments are not paid."""
        paid: dict[str, bool] = {}
        for external_id in external_ids:
            raw = self._client.get(self.company_url(f"/invoice/{external_id}"))
            invoice = raw.get("Invoice", raw)
            paid[external_id] = _cents(invoice.get("Balance", 0)) == 0
        return paid

    def remaining_balance(self, external_id: str) -> Money:
        raw = self._client.get(self.company_url(f"/invoice/{external_id}"))
        invoice = raw.get("Invoice", raw)
        return Money(max(_cents(invoice.get("Balance", 0)), 0))

    # --- helpers ---

    def company_url(self, suffix: str) -> str:
        return self._client.company_url(suffix)

    def invoice_pdf(self, quickbooks_id: str) -> bytes:
        content = self._client.get(
            self.company_url(f"/invoice/{quickbooks_id}/pdf"), accept="application/pdf"
        )
        assert isinstance(content, bytes)
        return content

    def delete_invoice(self, external_id: str) -> None:
        """Remove an invoice entirely, leaving no record of it.

        Only `fops qbo-test-invoice` uses this, to leave a company Icon is not
        yet using exactly as it found it. A real correction never deletes: it
        voids, so the invoice that went to a client stays visible in the books
        (docs/status-tracking.md).
        """
        raw = self._client.get(self.company_url(f"/invoice/{external_id}"))
        invoice = raw.get("Invoice", raw)
        logs.log("deleting a quickbooks invoice", quickbooks_id=external_id)
        self._client.post(
            self.company_url("/invoice?operation=delete"),
            json={"Id": external_id, "SyncToken": str(invoice.get("SyncToken", "0"))},
        )

    def _void(self, quickbooks_id: str, sync_token: str) -> None:
        logs.log("voiding a quickbooks invoice", quickbooks_id=quickbooks_id)
        self._client.post(
            self.company_url("/invoice?operation=void"),
            json={"Id": quickbooks_id, "SyncToken": sync_token},
        )

    def _invoice_body(self, invoice: Invoice, item_id: int) -> dict[str, Any]:
        hours = invoice.approved_hours.hundredths / 100
        # The client is looked up first, so a workbook naming a client
        # QuickBooks has never heard of says so before anything else.
        customer = self.customer_ref(invoice.quickbooks_customer or invoice.client_legal_name)
        # The rate comes off the consultant's product in QuickBooks, not off
        # the engagement list (decision 30). The total that comes back is
        # checked against the engagement list, which is what notices drift.
        product = self.product_for(invoice.consultant, client_names(invoice))
        rate = product.unit_price_cents / 100
        line_total = invoice_amount(invoice.approved_hours, Money(product.unit_price_cents))
        return {
            # The engagement list's "QuickBooks customer" column decides, since
            # QuickBooks often spells a company differently from the invoice.
            "CustomerRef": {"value": customer},
            # Kevin's number, not QuickBooks'. Needs "Custom transaction
            # numbers" on in the company settings, which create_invoice checks
            # by comparing what comes back.
            "DocNumber": invoice.number,
            "TxnDate": invoice.issue_date.isoformat(),
            "DueDate": invoice.due_date.isoformat(),
            "PrivateNote": private_note(item_id, invoice.replaces_number),
            # The agent sends the billing email itself; QuickBooks must not.
            "EmailStatus": "NotSet",
            "Line": [
                {
                    "DetailType": "SalesItemLineDetail",
                    # The product is the consultant, so the description Kevin's
                    # invoice template prints is their name.
                    "Description": invoice.consultant,
                    "Amount": line_total.cents / 100,
                    "SalesItemLineDetail": {
                        "ItemRef": {"value": product.ref},
                        # Kevin's template labels this column "period ending".
                        "ServiceDate": invoice.period.end.isoformat(),
                        "Qty": hours,
                        "UnitPrice": rate,
                        "TaxCodeRef": {"value": "NON"},
                    },
                }
            ],
        }
