"""An in-memory accounting system for tests.

Like both real adapters, it invoices under the number the application worked
out (docs/decisions.md #27) and keeps its own external id, which is the thing
the accounting system knows the invoice by.
"""

from collections import Counter
from collections.abc import Sequence

from finance_ops_agent.domain.invoices import Invoice
from finance_ops_agent.domain.setup import EngagementSetup
from finance_ops_agent.ports.accounting import (
    AccountingEngagement,
    AccountingParty,
    CreatedInvoice,
    EngagementListing,
    EngagementRates,
    SetupDone,
)


class FakeAccounting:
    def __init__(self) -> None:
        self.invoices: dict[int, CreatedInvoice] = {}
        self.cancelled: list[str] = []
        self.renamed: dict[str, str] = {}
        self.paid: set[str] = set()
        self.asked: list[str] = []
        # Tests set these to make the accounting system misbehave the way a
        # real one does: a refusal, or a connection that needs renewing.
        self.fail_with: Exception | None = None
        # Only creating an invoice fails: QuickBooks answered every question
        # about the engagement, then refused the invoice itself.
        self.fail_create_with: Exception | None = None
        self.fail_setup_with: Exception | None = None
        self.set_up: list[EngagementSetup] = []  # every setup actually made
        # Keyed by (consultant, client); tests set what the accounting system
        # says an engagement is billed and paid at.
        self.rates: dict[tuple[str, str], EngagementRates] = {}
        # Which engagements the accounting system says are live. Empty is what
        # every existing test wants: "nothing to say", so the engagement list
        # decides as it always has (docs/decisions.md #42).
        self.live: list[AccountingEngagement] = []
        # What the accounting system holds about a client, by name, and about a
        # payee, by the id an engagement's rates carry.
        self.customers: dict[str, AccountingParty] = {}
        self.payees: dict[str, AccountingParty] = {}
        self.create_attempts = 0
        # Every question asked, by method name: how tests see that a run asks
        # QuickBooks only about what is in front of it (decisions 54 and 55).
        self.calls: Counter[str] = Counter()
        self._counter = 0

    def create_invoice(self, invoice: Invoice, item_id: int) -> CreatedInvoice:
        self.calls["create_invoice"] += 1
        self.create_attempts += 1
        if self.fail_with is not None:
            raise self.fail_with
        if self.fail_create_with is not None:
            raise self.fail_create_with
        existing = self.find_invoice(item_id)
        if existing is not None:
            return existing
        self._counter += 1
        created = CreatedInvoice(
            number=invoice.number, external_id=f"ext-{self._counter}", pdf=b"%PDF-fake"
        )
        self.invoices[item_id] = created
        return created

    def customer(self, name: str) -> AccountingParty | None:
        self.calls["customer"] += 1
        if self.fail_with is not None:
            raise self.fail_with
        return self.customers.get(name)

    def payee(self, ref: str) -> AccountingParty | None:
        self.calls["payee"] += 1
        if self.fail_with is not None:
            raise self.fail_with
        return self.payees.get(ref)

    def engagements(self) -> EngagementListing:
        self.calls["engagements"] += 1
        if self.fail_with is not None:
            raise self.fail_with
        return EngagementListing(
            live=list(self.live), products_seen=len(self.live), categories_seen=len(self.live)
        )

    def engagement_rates(self, consultant: str, clients: Sequence[str]) -> EngagementRates | None:
        self.calls["engagement_rates"] += 1
        if self.fail_with is not None:
            raise self.fail_with
        for client in clients:
            found = self.rates.get((consultant, client))
            if found is not None:
                return found
        return None

    def find_invoice(self, item_id: int) -> CreatedInvoice | None:
        self.calls["find_invoice"] += 1
        created = self.invoices.get(item_id)
        if created is not None and created.external_id in self.cancelled:
            return None
        return created

    def cancel_invoice(self, external_id: str, renamed_to: str | None = None) -> None:
        self.calls["cancel_invoice"] += 1
        if self.fail_with is not None:
            raise self.fail_with
        self.cancelled.append(external_id)
        if renamed_to is not None:
            self.renamed[external_id] = renamed_to

    def paid_status(self, external_ids: list[str]) -> dict[str, bool]:
        self.calls["paid_status"] += 1
        if self.fail_with is not None:
            raise self.fail_with
        self.asked.extend(external_ids)
        return {external_id: external_id in self.paid for external_id in external_ids}

    def set_up_engagement(self, setup: EngagementSetup) -> SetupDone:
        """Find-or-create, as the real adapter does: a second call after the
        first succeeded creates nothing more."""
        self.calls["set_up_engagement"] += 1
        if self.fail_with is not None:
            raise self.fail_with
        if self.fail_setup_with is not None:
            raise self.fail_setup_with
        created: list[str] = []
        reused: list[str] = []
        if setup.client in self.customers:
            reused.append(f"customer {setup.client}")
        else:
            self.customers[setup.client] = AccountingParty(
                ref=f"c-{setup.client}",
                name=setup.client,
                company=setup.client_legal_name,
                email=setup.client_email,
                payment_terms_days=setup.client_pays_within_days,
                notes=f"Invoice code: {setup.invoice_code}",
            )
            created.append(f"customer {setup.client}")
        vendor_ref = next(
            (
                ref
                for ref, party in self.payees.items()
                if party.email.casefold() == setup.consultant_email.casefold()
            ),
            "",
        )
        if vendor_ref:
            reused.append(f"vendor {setup.consultant}")
        else:
            vendor_ref = f"v-{setup.consultant}"
            self.payees[vendor_ref] = AccountingParty(
                ref=vendor_ref,
                name=setup.consultant,
                company=setup.firm,
                email=setup.consultant_email,
                payment_terms_days=setup.pay_within_days,
            )
            created.append(f"vendor {setup.consultant}")
        ref = f"p-{setup.consultant}-{setup.client}"
        if any(live.ref == ref for live in self.live):
            reused.append(f"product {setup.client}:{setup.consultant}")
        else:
            self.live.append(
                AccountingEngagement(
                    ref=ref,
                    consultant=setup.consultant,
                    client=setup.client,
                    bill_rate_cents=setup.bill_rate.cents,
                    pay_rate_cents=setup.pay_rate.cents,
                    payee=setup.firm or setup.consultant,
                    payee_ref=vendor_ref,
                    notes=f"Start: {setup.start.isoformat()}",
                )
            )
            created.append(f"product {setup.client}:{setup.consultant}")
        self.rates[(setup.consultant, setup.client)] = EngagementRates(
            ref=ref,
            bill_rate_cents=setup.bill_rate.cents,
            pay_rate_cents=setup.pay_rate.cents,
            payee=setup.firm or setup.consultant,
            payee_ref=vendor_ref,
        )
        self.set_up.append(setup)
        return SetupDone(created=created, reused=reused)
