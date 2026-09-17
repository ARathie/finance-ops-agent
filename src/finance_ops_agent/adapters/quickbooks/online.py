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

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from finance_ops_agent import logs
from finance_ops_agent.adapters.quickbooks.client import (
    QuickBooksClient,
    QuickBooksFailed,
    with_trace,
)
from finance_ops_agent.domain.invoices import Invoice
from finance_ops_agent.domain.money import Money, invoice_amount
from finance_ops_agent.ports.accounting import CreatedInvoice

PRIVATE_NOTE_PREFIX = "fops item"


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


def _cents(amount: Any) -> int:
    """QuickBooks sends money as a JSON number; compare in whole cents only."""
    return round(float(amount) * 100)


@dataclass(frozen=True)
class Product:
    """A consultant's product in QuickBooks, and the rate on it."""

    ref: str
    name: str
    unit_price_cents: int


class QuickBooksOnline:
    def __init__(self, client: QuickBooksClient, today: date) -> None:
        self._client = client
        self._today = today  # from the Clock port, never the system clock
        self._customers: dict[str, str] = {}
        self._products: dict[str, Product] = {}

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
        escaped = quickbooks_customer.replace("'", "\\'")
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

    def product_for(self, consultant: str) -> Product:
        """The consultant's own product, which is where the rate lives now.

        Looked up by the consultant's name exactly as the engagement list
        spells it (decision 30). The agent never creates products, and never
        falls back to another one: billing a consultant under someone else's
        product would bill the wrong rate.
        """
        if consultant in self._products:
            return self._products[consultant]
        escaped = consultant.replace("'", "\\'")
        rows = self._client.query(f"SELECT Id, Name, UnitPrice FROM Item WHERE Name = '{escaped}'")
        if not rows:
            logs.log("quickbooks product not found", consultant=consultant)
            raise QuickBooksFailed(
                f"QuickBooks has no product called {consultant!r}. Every consultant"
                " needs their own product, with their rate on it, and its name has to"
                " match the Consultants sheet exactly. I never create products myself."
            )
        if rows[0].get("UnitPrice") is None:
            logs.log("quickbooks product has no rate", consultant=consultant)
            raise QuickBooksFailed(
                f"The QuickBooks product for {consultant!r} has no rate on it, and the"
                " rate on the product is what I bill. Put their hourly rate on the"
                " product in QuickBooks."
            )
        product = Product(
            ref=str(rows[0]["Id"]),
            name=str(rows[0].get("Name") or consultant),
            unit_price_cents=_cents(rows[0]["UnitPrice"]),
        )
        logs.log("quickbooks product found", consultant=consultant, quickbooks_id=product.ref)
        self._products[consultant] = product
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
            quickbooks_id = str(row["Id"])
            return CreatedInvoice(
                number=str(row.get("DocNumber") or quickbooks_id),
                external_id=quickbooks_id,
                pdf=self.invoice_pdf(quickbooks_id),
            )
        return None

    def cancel_invoice(self, external_id: str) -> None:
        raw = self._client.get(self.company_url(f"/invoice/{external_id}"))
        invoice = raw.get("Invoice", raw)
        self._void(external_id, str(invoice.get("SyncToken", "0")))

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
        product = self.product_for(invoice.consultant)
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
