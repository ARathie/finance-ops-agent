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
from finance_ops_agent.domain.setup import EngagementSetup
from finance_ops_agent.ports.accounting import (
    AccountingEngagement,
    AccountingParty,
    CreatedInvoice,
    EngagementListing,
    EngagementRates,
    SetupDone,
)

PRIVATE_NOTE_PREFIX = "fops item"
# Creating a category needs a record shape newer than the default. Asked for
# only on the calls that set an engagement up, and only when the operator has
# not chosen a version already (decision 56).
SETUP_MINORVERSION = "75"
# A sentinel for "not asked yet", because "asked, and the company has no
# default" is a real and different answer that must not be asked again.
_UNREAD: Any = object()
# The whole entity, not a field list. QuickBooks' query language refuses
# `PrefVendorRef` in a SELECT ("Property PrefVendorRef not found for Entity
# Item"): references come back with the entity or not at all. Asking for the
# entity also means this does not have to be kept in step with which fields
# the query language happens to accept.
PRODUCT_FIELDS = "*"
# QuickBooks caps a query at 1000 rows and pages with STARTPOSITION, which is
# 1-based. Icon has a handful of products; the paging is here so that a company
# with a real catalogue does not quietly return the first page and look as
# though the rest of the engagements had ended.
PRODUCT_PAGE = 1000
# A bound on the loop, not on the catalogue: a server that kept answering with
# a full page would otherwise spin for ever.
MAX_PRODUCT_PAGES = 20


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


def _clean(value: Any) -> str:
    """A name or address from QuickBooks, as text that can be compared.

    QuickBooks keeps whatever was typed or pasted, and a name pasted from a
    document arrives with a non-breaking space on the end -- `"Subramanian
    Arumugam\xa0"`. It looks identical to the plain name on every screen and
    is not equal to it, so an invisible character became a disagreement nobody
    could see, and would have become a payee name with one in it. Every name
    and address read from QuickBooks comes through here.
    """
    return " ".join(str(value or "").split())


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


def _engagement_from(
    row: dict[str, Any], categories: dict[str, str]
) -> "AccountingEngagement | None":
    """One row of an item listing, as an engagement -- or None if it is not one.

    A category is itself an Item in QuickBooks, so the listing contains both the
    engagements and the clients they sit under; a category is not an engagement.
    Nor is a product that sits under nothing.

    **The parent is found by `ParentRef` first, and only then by splitting
    `FullyQualifiedName`.** The two are meant to say the same thing, but whether
    a category shows up in the fully qualified name depends on the API version
    being talked to, while `ParentRef` is the relationship itself. Reading the
    name alone reported "no product has a category" against a company where
    every product had one.
    """
    if str(row.get("Type") or "") == "Category":
        return None
    path = str(row.get("FullyQualifiedName") or "")
    parent = row.get("ParentRef") or {}
    client = ""
    if isinstance(parent, dict) and parent.get("value"):
        reference = str(parent["value"])
        client = categories.get(reference) or str(parent.get("name") or "")
    if not client and ":" in path:
        client = path.rpartition(":")[0]
    # Nested deeper than one level: the client is the category it sits directly
    # under, which is the same one `product_for` looks a product up by.
    client = _clean(client.rpartition(":")[2] or client)
    if not client:
        return None
    price = row.get("UnitPrice")
    cost = row.get("PurchaseCost")
    vendor = row.get("PrefVendorRef") or {}
    return AccountingEngagement(
        ref=str(row["Id"]),
        consultant=_clean(row.get("Name") or path.rpartition(":")[2]),
        client=client,
        bill_rate_cents=None if price is None else _cents(price),
        pay_rate_cents=None if cost is None else _cents(cost),
        payee=_clean(vendor.get("name")) if isinstance(vendor, dict) else "",
        payee_ref=str(vendor.get("value") or "") if isinstance(vendor, dict) else "",
        notes=str(row.get("PurchaseDesc") or ""),
    )


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
    vendor_ref: str = ""  # the vendor's own id, to read their email and terms
    # A vendor's display name is often the consultant and its company name the
    # firm Icon actually pays. Both are held, and either matching the
    # engagement list's payee is agreement (docs/decisions.md #46).
    vendor_company: str = ""
    kind: str = ""  # Service, NonInventory, Inventory -- QuickBooks' own word


class QuickBooksOnline:
    def __init__(self, client: QuickBooksClient, today: date) -> None:
        self._client = client
        self._today = today  # from the Clock port, never the system clock
        self._customers: dict[str, str] = {}
        self._products: dict[tuple[str, tuple[str, ...]], Product] = {}
        self._terms: dict[str, int] | None = None
        self._parents: dict[str, str] = {}
        self._vendors: dict[str, tuple[str, str]] = {}
        # Unread, as distinct from read and found to be nothing.
        self._company_default: int | None = _UNREAD

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
                " create a customer on my own, only when you fill in my setup form"
                " and confirm it."
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

        # The path did not find it. That is not proof there is no category:
        # whether one shows up in `FullyQualifiedName` depends on the API
        # version being talked to, and `ParentRef` is the relationship itself.
        # So ask by name, then read each candidate's parent.
        rows = self._client.query(
            f"SELECT {PRODUCT_FIELDS} FROM Item WHERE Name = '{_escape(consultant)}'"
        )
        if len(rows) > 1:
            wanted = {name.casefold() for name in clients if name}
            under_the_client = [row for row in rows if self._parent_name(row).casefold() in wanted]
            if len(under_the_client) == 1:
                return self._remember(
                    key, consultant, under_the_client[0], self._parent_name(under_the_client[0])
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
                " a product on my own, only when you fill in my setup form and"
                " confirm it."
            )
        return self._remember(key, consultant, rows[0], self._parent_name(rows[0]))

    def _vendor_names(self, ref: str) -> tuple[str, str]:
        """A vendor's display name and its company name.

        `PrefVendorRef` on a product carries only the display name, which for a
        consultant working through a firm is the person. Who Icon pays is the
        firm, and that is the company name -- so both are read, and both count
        as agreement with the engagement list's payee.
        """
        if ref not in self._vendors:
            rows = self._client.query(f"SELECT * FROM Vendor WHERE Id = '{_escape(ref)}'")
            self._vendors[ref] = (
                (_clean(rows[0].get("DisplayName")), _clean(rows[0].get("CompanyName")))
                if rows
                else ("", "")
            )
        return self._vendors[ref]

    def _parent_name(self, row: dict[str, Any]) -> str:
        """The category a product sits directly under, or "".

        `ParentRef` sometimes carries the parent's name and sometimes only its
        id, so the id is resolved and the answers are kept for the run: a
        company where every consultant works for the same client would
        otherwise ask for the same category once per engagement.
        """
        parent = row.get("ParentRef") or {}
        if not isinstance(parent, dict) or not parent.get("value"):
            return ""
        named = _clean(parent.get("name"))
        if not named:
            reference = str(parent["value"])
            if reference not in self._parents:
                found = self._client.query(
                    f"SELECT {PRODUCT_FIELDS} FROM Item WHERE Id = '{_escape(reference)}'"
                )
                self._parents[reference] = (
                    _clean(found[0].get("FullyQualifiedName") or found[0].get("Name"))
                    if found
                    else ""
                )
            named = self._parents[reference]
        return _clean(named.rpartition(":")[2] or named)

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
        vendor_ref = str(vendor.get("value") or "") if isinstance(vendor, dict) else ""
        product = Product(
            ref=str(row["Id"]),
            name=_clean(row.get("FullyQualifiedName") or row.get("Name") or consultant),
            unit_price_cents=_cents(row["UnitPrice"]),
            purchase_cost_cents=None if cost is None else _cents(cost),
            vendor=_clean(vendor.get("name")) if isinstance(vendor, dict) else "",
            vendor_ref=vendor_ref,
            vendor_company=self._vendor_names(vendor_ref)[1] if vendor_ref else "",
            kind=_clean(row.get("Type")),
        )
        logs.log(
            "quickbooks product found",
            consultant=consultant,
            quickbooks_id=product.ref,
            matched_on=path or "the name alone, with no category I could read",
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
            # not survive, and it must never reach a client. The agent priced
            # this from the product when the timesheet was read (decision 43),
            # so what is left for this to catch is the product's rate having
            # been changed between then and now -- a window that opens because
            # the invoice is not made until Kevin approves.
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

    def _term_days(self) -> dict[str, int]:
        """Every payment term in the company, by id.

        QuickBooks keeps terms as their own entity and puts only a reference on
        a customer or a vendor, so the days have to be looked up. There are a
        handful of them and they change about never, so they are read once per
        run rather than per party.
        """
        if self._terms is None:
            self._terms = {}
            # The whole entity, for the reason PRODUCT_FIELDS gives: a field
            # list is one more thing that has to stay in step with what
            # QuickBooks' query language happens to accept.
            for row in self._client.query("SELECT * FROM Term"):
                days = row.get("DueDays")
                if days is not None:
                    self._terms[str(row["Id"])] = int(days)
            logs.log("quickbooks terms read", count=len(self._terms))
        return self._terms

    def _company_terms(self) -> int | None:
        """The terms QuickBooks itself would put on an invoice by default.

        A customer's own `SalesTermRef` is only set when someone chose terms on
        that customer. The Customer Details screen shows terms either way,
        because it shows what an invoice would get -- which is the company
        default from Account and settings when the record names none. Reading
        the default is what makes the agent's due date the one QuickBooks would
        have worked out (docs/decisions.md #47).
        """
        if self._company_default is _UNREAD:
            self._company_default = None
            rows = self._client.query("SELECT * FROM Preferences")
            if rows:
                sales = rows[0].get("SalesFormsPrefs") or {}
                term = sales.get("DefaultTerms") or {} if isinstance(sales, dict) else {}
                if isinstance(term, dict) and term.get("value"):
                    self._company_default = self._term_days().get(str(term["value"]))
            logs.log("quickbooks company default terms read", days=self._company_default)
        return self._company_default

    def _party(self, row: dict[str, Any], terms_field: str) -> AccountingParty:
        email = row.get("PrimaryEmailAddr") or {}
        term = row.get(terms_field) or {}
        days: int | None = None
        note = ""
        reference = str(term.get("value") or "") if isinstance(term, dict) else ""
        if not reference:
            # Nothing on the record: fall back to what QuickBooks would do,
            # which is the company default. Only when there is no default
            # either is there nothing to compare.
            days = self._company_terms()
            if days is None:
                note = "no terms on the record and no company default"
        else:
            days = self._term_days().get(reference)
            if days is None:
                named = str(term.get("name") or reference) if isinstance(term, dict) else reference
                note = f"the term {named!r} has no number of days on it"
        return AccountingParty(
            ref=str(row["Id"]),
            name=_clean(row.get("DisplayName") or row.get("CompanyName")),
            company=_clean(row.get("CompanyName")),
            email=_clean(email.get("Address")) if isinstance(email, dict) else "",
            payment_terms_days=days,
            terms_note=note,
            notes=str(row.get("Notes") or ""),
        )

    def customer(self, name: str) -> AccountingParty | None:
        """The client's own record: where invoices go, and how long they have
        to pay. Found the same way `customer_ref` finds it, so the two never
        disagree about which customer a name means."""
        reference = self.customer_ref(name)
        rows = self._client.query(f"SELECT * FROM Customer WHERE Id = '{_escape(reference)}'")
        return self._party(rows[0], "SalesTermRef") if rows else None

    def payee(self, ref: str) -> AccountingParty | None:
        """The vendor on a product's purchase side: who Icon pays, where to
        reach them, and how long Icon has to pay."""
        if not ref:
            return None
        rows = self._client.query(f"SELECT * FROM Vendor WHERE Id = '{_escape(ref)}'")
        return self._party(rows[0], "TermRef") if rows else None

    def engagements(self) -> EngagementListing:
        """Every active product under a category: one per live engagement.

        This is the other direction from `product_for`. That one asks "what is
        this engagement billed at?"; this one asks "which engagements are
        there?", which is what lets QuickBooks say an engagement has finished
        by the product being made inactive (decision 42).

        Only products under a category count. A product with no category is not
        an engagement -- it is a service Icon sells, or a product half set up.
        Everything read is counted, because "no engagements" and "no products
        at all" need different things done about them.

        `Active = true` is asked for explicitly rather than relied on: an
        inactive product coming back would read as a live engagement.
        """
        rows: list[dict[str, Any]] = []
        start = 1
        for _ in range(MAX_PRODUCT_PAGES):
            page = self._client.query(
                f"SELECT {PRODUCT_FIELDS} FROM Item WHERE Active = true"
                f" STARTPOSITION {start} MAXRESULTS {PRODUCT_PAGE}"
            )
            rows.extend(page)
            if len(page) < PRODUCT_PAGE:
                break
            start += len(page)
        else:
            logs.log("stopped paging through quickbooks products", pages=MAX_PRODUCT_PAGES)

        # Every row first, so a product's parent can be named even when the
        # reference carries only an id: the categories are in this same listing.
        categories = {
            str(row["Id"]): _clean(row.get("FullyQualifiedName") or row.get("Name"))
            for row in rows
            if str(row.get("Type") or "") == "Category"
        }
        # A parent that is not itself in the listing is still a parent: ask for
        # it by id rather than dropping the product. Whatever the reason a
        # category does not come back as an item, a product that plainly has a
        # parent must not read as one with no category.
        for row in rows:
            parent = row.get("ParentRef") or {}
            if not isinstance(parent, dict) or not parent.get("value"):
                continue
            reference = str(parent["value"])
            if reference not in categories and not parent.get("name"):
                categories[reference] = self._parent_name(row)
        found = [
            engagement
            for engagement in (_engagement_from(row, categories) for row in rows)
            if engagement is not None
        ]
        typed = sum(1 for row in rows if str(row.get("Type") or "") == "Category")
        listing = EngagementListing(
            live=found,
            products_seen=len(rows) - typed,
            categories_seen=len({name for name in categories.values() if name}),
            categories_that_exist=typed,
        )
        logs.log(
            "quickbooks engagements listed",
            count=len(found),
            products_seen=listing.products_seen,
            categories_seen=listing.categories_seen,
        )
        return listing

    def product_kind(self, consultant: str, clients: Sequence[str] = ()) -> str:
        """What kind of product QuickBooks holds this engagement as.

        Only the doctor asks. An Inventory product counts a quantity on hand
        and posts to stock and cost of goods sold, which is not what an hour of
        someone's time is; invoices are still made from it, so this is
        something to say rather than something to refuse.
        """
        try:
            return self.product_for(consultant, clients).kind
        except QuickBooksFailed:
            return ""

    def engagement_rates(self, consultant: str, clients: Sequence[str]) -> EngagementRates | None:
        """Both sides of the engagement's product (decision 38)."""
        product = self.product_for(consultant, clients)
        return EngagementRates(
            ref=product.ref,
            bill_rate_cents=product.unit_price_cents,
            pay_rate_cents=product.purchase_cost_cents,
            payee=product.vendor_company or product.vendor,
            payee_ref=product.vendor_ref,
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

    # --- setting an engagement up (decision 56) ---

    def set_up_engagement(self, setup: EngagementSetup) -> SetupDone:
        """Find each record first and create only what is missing.

        The order is the order things depend on each other: terms before the
        customer and vendor that name them, the category before the product
        that sits under it. A failure part-way leaves what was made in place,
        and the next attempt finds it and carries on."""
        done = SetupDone(created=[], reused=[])
        customer_id = self._set_up_customer(setup, done)
        category_id = self._set_up_category(setup.client, done)
        vendor_id = self._set_up_vendor(setup, done)
        self._set_up_product(setup, category_id, vendor_id, done)
        logs.log(
            "quickbooks engagement set up",
            customer_id=customer_id,
            created=len(done.created),
            reused=len(done.reused),
        )
        return done

    def _setup_url(self, suffix: str) -> str:
        url = self.company_url(suffix)
        if "minorversion=" not in url:
            url += ("&" if "?" in url else "?") + f"minorversion={SETUP_MINORVERSION}"
        return url

    def _created(self, entity: str, body: dict[str, Any]) -> dict[str, Any]:
        raw = self._client.post(self._setup_url(f"/{entity.lower()}"), json=body)
        row: dict[str, Any] = raw.get(entity, raw)
        return row

    def _term_for(self, days: int, done: SetupDone) -> str:
        """The id of a payment term of exactly `days` days, made if there is none."""
        for reference, term_days in self._term_days().items():
            if term_days == days:
                return reference
        row = self._created("Term", {"Name": f"Net {days}", "DueDays": days})
        reference = str(row["Id"])
        self._term_days()[reference] = days
        done.created.append(f"payment term Net {days}")
        return reference

    def _set_up_customer(self, setup: EngagementSetup, done: SetupDone) -> str:
        try:
            reference = self.customer_ref(setup.client)
        except QuickBooksFailed as error:
            if "has no customer" not in str(error):
                raise  # ambiguous: never guessed between
            if not setup.new_client:
                raise
            body: dict[str, Any] = {
                "DisplayName": setup.client,
                "CompanyName": setup.client_legal_name or setup.client,
                "PrimaryEmailAddr": {"Address": setup.client_email},
                "SalesTermRef": {"value": self._term_for(setup.client_pays_within_days, done)},
                "Notes": f"Invoice code: {setup.invoice_code}",
            }
            reference = str(self._created("Customer", body)["Id"])
            self._customers[setup.client] = reference
            done.created.append(f"customer {setup.client}")
            return reference
        done.reused.append(f"customer {setup.client}")
        if setup.new_client and setup.invoice_code:
            # Already in QuickBooks but with no invoice code, which is why the
            # agent did not know it: the code is added, nothing else changed.
            rows = self._client.query(f"SELECT * FROM Customer WHERE Id = '{_escape(reference)}'")
            notes = str(rows[0].get("Notes") or "") if rows else ""
            if rows and "invoice code:" not in notes.casefold():
                self._client.post(
                    self._setup_url("/customer"),
                    json={
                        "Id": reference,
                        "SyncToken": rows[0]["SyncToken"],
                        "sparse": True,
                        "Notes": (notes + "\n" if notes else "")
                        + f"Invoice code: {setup.invoice_code}",
                    },
                )
                done.created.append(f"invoice code {setup.invoice_code} on {setup.client}")
        return reference

    def _set_up_category(self, client: str, done: SetupDone) -> str:
        rows = self._client.query(f"SELECT * FROM Item WHERE Name = '{_escape(client)}'")
        for row in rows:
            if str(row.get("Type") or "") == "Category":
                done.reused.append(f"category {client}")
                return str(row["Id"])
        row = self._created("Item", {"Name": client, "Type": "Category"})
        done.created.append(f"category {client}")
        return str(row["Id"])

    def _set_up_vendor(self, setup: EngagementSetup, done: SetupDone) -> str:
        rows = self._client.query(
            f"SELECT * FROM Vendor WHERE DisplayName = '{_escape(setup.consultant)}'"
        )
        if rows:
            row = rows[0]
            email = _clean((row.get("PrimaryEmailAddr") or {}).get("Address"))
            if email and email.casefold() != setup.consultant_email.casefold():
                raise QuickBooksFailed(
                    f"QuickBooks already has a vendor called {setup.consultant!r}, with"
                    f" the email {email}, not {setup.consultant_email}. I won't change"
                    " who an existing vendor is. Fix the vendor in QuickBooks, then"
                    ' reply "try again".'
                )
            if not email:
                self._client.post(
                    self._setup_url("/vendor"),
                    json={
                        "Id": row["Id"],
                        "SyncToken": row["SyncToken"],
                        "sparse": True,
                        "PrimaryEmailAddr": {"Address": setup.consultant_email},
                    },
                )
                done.created.append(f"email {setup.consultant_email} on vendor {setup.consultant}")
            done.reused.append(f"vendor {setup.consultant}")
            return str(row["Id"])
        body: dict[str, Any] = {
            "DisplayName": setup.consultant,
            "PrimaryEmailAddr": {"Address": setup.consultant_email},
            "TermRef": {"value": self._term_for(setup.pay_within_days, done)},
        }
        if setup.firm:
            body["CompanyName"] = setup.firm
        row = self._created("Vendor", body)
        done.created.append(f"vendor {setup.consultant}")
        return str(row["Id"])

    def _accounts_from_another_engagement(self) -> tuple[dict[str, Any], dict[str, Any]]:
        """The income and expense accounts Icon's engagements already use.

        A product needs both and nothing in Kevin's answers says which, so a
        new engagement is filed like the existing ones. With none to copy, it
        is refused rather than guessed."""
        rows = self._client.query(
            f"SELECT {PRODUCT_FIELDS} FROM Item WHERE Active = true MAXRESULTS {PRODUCT_PAGE}"
        )
        for row in rows:
            income, expense = row.get("IncomeAccountRef"), row.get("ExpenseAccountRef")
            if row.get("ParentRef") and income and expense:
                return dict(income), dict(expense)
        raise QuickBooksFailed(
            "I couldn't find an engagement product in QuickBooks with both an income"
            " and an expense account to copy, so I don't know which accounts a new"
            " one belongs in. Set one engagement up by hand first."
        )

    def _set_up_product(
        self, setup: EngagementSetup, category_id: str, vendor_id: str, done: SetupDone
    ) -> None:
        path = f"{setup.client}:{setup.consultant}"
        rows = self._client.query(
            f"SELECT {PRODUCT_FIELDS} FROM Item WHERE FullyQualifiedName = '{_escape(path)}'"
        )
        if rows:
            row = rows[0]
            if row.get("UnitPrice") is not None and _cents(row["UnitPrice"]) != (
                setup.bill_rate.cents
            ):
                raise QuickBooksFailed(
                    f"QuickBooks already has the product {path} at"
                    f" ${Money(_cents(row['UnitPrice']))} an hour, not ${setup.bill_rate}."
                    " I won't change a rate that is already there. Fix it in QuickBooks,"
                    ' then reply "try again".'
                )
            done.reused.append(f"product {path}")
            return
        income, expense = self._accounts_from_another_engagement()
        self._created(
            "Item",
            {
                "Name": setup.consultant,
                "Type": "Service",
                "SubItem": True,
                "ParentRef": {"value": category_id},
                "UnitPrice": setup.bill_rate.cents / 100,
                "PurchaseCost": setup.pay_rate.cents / 100,
                "PrefVendorRef": {"value": vendor_id},
                "PurchaseDesc": f"Start: {setup.start.isoformat()}",
                "IncomeAccountRef": income,
                "ExpenseAccountRef": expense,
            },
        )
        done.created.append(f"product {path}")
        self._products.clear()  # the next lookup must see the new one

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
