"""QuickBooks Online as the AccountingSystem (FOPS_ACCOUNTING=quickbooks).

Three rules from docs/integrations/quickbooks-online.md carry the weight:

- The total QuickBooks returns must equal the agent's own amount to the cent.
  If it does not, the invoice is voided immediately and QUICKBOOKS_FAILED is
  raised: an invoice the agent cannot vouch for never reaches a client.
- The item id goes in PrivateNote, which is how the agent recognises an
  invoice it already created after a crash, instead of creating a second one.
- EmailStatus is NotSet: the agent sends the billing email itself, so
  QuickBooks must never also send one or the client would get two.
"""

from datetime import date, timedelta
from typing import Any

from finance_ops_agent.adapters.quickbooks.client import QuickBooksClient, QuickBooksFailed
from finance_ops_agent.domain.invoices import Invoice
from finance_ops_agent.domain.money import Money
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


class QuickBooksOnline:
    def __init__(self, client: QuickBooksClient, item_name: str, today: date) -> None:
        self._client = client
        self._item_name = item_name
        self._today = today  # from the Clock port, never the system clock
        self._customers: dict[str, str] = {}
        self._item_ref: str | None = None

    # --- lookups, cached for the run ---

    def customer_ref(self, quickbooks_customer: str) -> str:
        if quickbooks_customer in self._customers:
            return self._customers[quickbooks_customer]
        escaped = quickbooks_customer.replace("'", "\\'")
        rows = self._client.query(
            f"SELECT Id, DisplayName FROM Customer WHERE DisplayName = '{escaped}'"
        )
        if not rows:
            raise QuickBooksFailed(
                f"QuickBooks has no customer called {quickbooks_customer!r}."
                ' Add it in QuickBooks, or fix the "QuickBooks customer" column'
                " in the engagement list. I never create customers myself."
            )
        reference = str(rows[0]["Id"])
        self._customers[quickbooks_customer] = reference
        return reference

    def item_ref(self) -> str:
        if self._item_ref is None:
            escaped = self._item_name.replace("'", "\\'")
            rows = self._client.query(f"SELECT Id, Name FROM Item WHERE Name = '{escaped}'")
            if not rows:
                raise QuickBooksFailed(
                    f"QuickBooks has no service item called {self._item_name!r}."
                    " Create it, or set QBO_ITEM_NAME to the one you use."
                )
            self._item_ref = str(rows[0]["Id"])
        return self._item_ref

    # --- the AccountingSystem port ---

    def create_invoice(self, invoice: Invoice, item_id: int) -> CreatedInvoice:
        existing = self.find_invoice(item_id)
        if existing is not None:
            return existing  # a crash left one behind; never create a second
        body = self._invoice_body(invoice, item_id)
        created = self._client.post(self.company_url("/invoice"), json=body)
        raw = created.get("Invoice", created)
        quickbooks_id = str(raw["Id"])
        total_cents = _cents(raw.get("TotalAmt", 0))

        if total_cents != invoice.total.cents:
            # Void first, then report: an amount the agent cannot vouch for must
            # not survive, and it must never reach a client.
            sync_token = str(raw.get("SyncToken", "0"))
            self._void(quickbooks_id, sync_token)
            raise QuickBooksFailed(
                f"QuickBooks totalled invoice {raw.get('DocNumber', quickbooks_id)} at"
                f" ${Money(total_cents)}, but this timesheet comes to"
                f" ${invoice.total}. I voided it and sent nothing to the client."
                " This usually means the item, rate, or tax settings in QuickBooks"
                " differ from the engagement list."
            )
        return CreatedInvoice(
            number=str(raw.get("DocNumber") or quickbooks_id),
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

    def _void(self, quickbooks_id: str, sync_token: str) -> None:
        self._client.post(
            self.company_url("/invoice?operation=void"),
            json={"Id": quickbooks_id, "SyncToken": sync_token},
        )

    def _invoice_body(self, invoice: Invoice, item_id: int) -> dict[str, Any]:
        hours = invoice.approved_hours.hundredths / 100
        rate = invoice.bill_rate.cents / 100
        return {
            # The engagement list's "QuickBooks customer" column decides, since
            # QuickBooks often spells a company differently from the invoice.
            "CustomerRef": {
                "value": self.customer_ref(invoice.quickbooks_customer or invoice.client_legal_name)
            },
            "TxnDate": invoice.issue_date.isoformat(),
            "DueDate": invoice.due_date.isoformat(),
            "PrivateNote": private_note(item_id, invoice.replaces_number),
            # The agent sends the billing email itself; QuickBooks must not.
            "EmailStatus": "NotSet",
            "Line": [
                {
                    "DetailType": "SalesItemLineDetail",
                    "Description": invoice.line_description(),
                    "Amount": invoice.total.cents / 100,
                    "SalesItemLineDetail": {
                        "ItemRef": {"value": self.item_ref()},
                        "Qty": hours,
                        "UnitPrice": rate,
                        "TaxCodeRef": {"value": "NON"},
                    },
                }
            ],
        }
