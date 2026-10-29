"""The engagement list, built from QuickBooks alone (decision 53).

Everything the agent needs about an engagement now has a place in QuickBooks:

- the engagement is an active product under a category named for the client
  (decisions 36 and 42), with both rates on it (30 and 38);
- the client is the customer the category is named for: its email, its terms,
  and an `Invoice code:` line in its Notes box;
- who Icon pays is the vendor on the product's purchase side, and that vendor's
  email is the address the consultant's timesheets come from;
- a product with no vendor and no cost is a salaried Icon employee (decision
  69): nothing is owed by the hour, and their address is an `Email:` line;
- the engagement's own lines -- `Start:` and, rarely, a schedule -- are in the
  product's "Description on purchase forms".

The result is the same `EngagementWorkbook` the spreadsheet parses to, so every
rule downstream is unchanged. What cannot be built is a problem naming the
QuickBooks record and what to fill in, and that engagement is left out, exactly
as a bad spreadsheet row is: nothing is ever billed from half an answer.

Defaults for what Icon has not needed yet: delivery by email, no CC, monthly,
never sent automatically, paid by bank transfer.
"""

from dataclasses import dataclass, replace

from finance_ops_agent.domain.checks import split_addresses
from finance_ops_agent.domain.engagements import (
    Client,
    Consultant,
    ConsultantType,
    Delivery,
    Engagement,
    EngagementWorkbook,
    ListRowProblem,
    PaidBy,
    Vendor,
)
from finance_ops_agent.domain.money import Money
from finance_ops_agent.domain.setup_notes import ProductSetup, customer_setup, product_setup
from finance_ops_agent.ports.accounting import (
    AccountingEngagement,
    AccountingFailed,
    AccountingNeedsReconnect,
    AccountingParty,
    AccountingSystem,
)

SHEET = "QuickBooks"  # where a problem says it is; there is no row number
DEFAULT_PAID_BY = PaidBy.BANK_TRANSFER


@dataclass(frozen=True)
class _Party:
    record: AccountingParty | None
    failure: str = ""


def _problem(message: str) -> ListRowProblem:
    return ListRowProblem(sheet=SHEET, row_number=0, message=message)


def workbook_from_accounting(accounting: AccountingSystem) -> EngagementWorkbook:
    """Every live engagement QuickBooks can fully describe, and what stops the rest.

    Raises `AccountingFailed` when QuickBooks cannot be asked at all, so the
    caller can say so rather than conclude Icon has no engagements.
    """
    listing = accounting.engagements().live
    customers: dict[str, _Party] = {}
    payees: dict[str, _Party] = {}

    def customer(category: str) -> _Party:
        if category not in customers:
            try:
                customers[category] = _Party(accounting.customer(category))
            except AccountingNeedsReconnect:
                raise
            except AccountingFailed as error:
                customers[category] = _Party(None, str(error))
        return customers[category]

    def payee(ref: str) -> _Party:
        if ref not in payees:
            try:
                payees[ref] = _Party(accounting.payee(ref))
            except AccountingNeedsReconnect:
                raise
            except AccountingFailed as error:
                payees[ref] = _Party(None, str(error))
        return payees[ref]

    problems: list[ListRowProblem] = []
    clients: dict[str, Client] = {}
    bad_clients: set[str] = set()
    for category in dict.fromkeys(engagement.client for engagement in listing):
        built = _client(category, customer(category))
        if isinstance(built, Client):
            clients[category] = built
            if built.delivery is Delivery.EMAIL and not built.billing_emails:
                # Still a client (decision 65): only its invoices wait.
                problems.append(
                    _problem(
                        f"customer {built.quickbooks_customer}: no email, so its invoices"
                        " wait until there is one"
                    )
                )
        else:
            problems.extend(built)
            bad_clients.add(category)
    shared = _shared_invoice_codes(clients)
    for category, _code in shared:
        bad_clients.add(category)
        clients.pop(category, None)
    problems.extend(
        _problem(said) for said in dict.fromkeys(message for _category, message in shared)
    )

    consultants: dict[str, Consultant] = {}
    vendors: dict[str, Vendor] = {}
    engagements: list[Engagement] = []
    for engagement in listing:
        who = f"product {engagement.consultant} under {engagement.client}"
        if engagement.client in bad_clients:
            continue  # the customer's own problem already says why
        paid = payee(engagement.payee_ref) if engagement.payee_ref else None
        found = _engagement(engagement, who, paid)
        if isinstance(found, list):
            problems.extend(found)
            continue
        row, vendor, emails = found
        earlier = consultants.get(row.consultant)
        if earlier is not None and earlier.salaried != (vendor is None):
            # One product says a salary and another a vendor: which is how this
            # person is paid is not guessed (decision 69).
            problems.append(
                _problem(
                    f"{who}: {row.consultant} has a vendor on one product and none on"
                    " another, so I cannot tell whether they are paid by the hour or a"
                    " salary. Give every one of their products a vendor and a cost, or"
                    " none of them."
                )
            )
            continue
        engagements.append(replace(row, row_number=len(engagements) + 1))
        if vendor is None:
            consultants[row.consultant] = _salaried_employee(earlier, row.consultant, emails)
            continue
        consultants[row.consultant] = _merged_consultant(earlier, row.consultant, vendor)
        # The vendor row keeps no addresses: they are on the consultant, so a
        # sender names a person rather than a firm that may supply several.
        vendors.setdefault(vendor.company, replace(vendor, contact_emails=()))
    used = {engagement.client for engagement in engagements}
    return EngagementWorkbook(
        clients=[client for name, client in clients.items() if name in used],
        consultants=list(consultants.values()),
        vendors=list(vendors.values()),
        engagements=engagements,
        problems=problems,
    )


def _client(category: str, held: _Party) -> Client | list[ListRowProblem]:
    if held.record is None:
        reason = held.failure or "QuickBooks has no customer by that name"
        return [
            _problem(
                f"category {category}: I need the customer this category is for, and"
                f" found none ({reason}). Name the category exactly as the customer's"
                " display name or company name in QuickBooks."
            )
        ]
    record = held.record
    setup = customer_setup(record.notes)
    problems = [f"customer {record.name}: {said}" for said in setup.problems]
    emails = tuple(split_addresses(record.email))
    if record.payment_terms_days is None:
        problems.append(
            f"customer {record.name}: no payment terms"
            f" ({record.terms_note or 'none on the record'})"
        )
    if problems:
        return [_problem(said) for said in problems]
    assert record.payment_terms_days is not None
    return Client(
        name=category,
        legal_name=record.company or record.name,
        billing_contact="",
        billing_emails=emails,
        cc_emails=setup.cc_emails,
        payment_terms_days=record.payment_terms_days,
        delivery=setup.delivery,
        time_system="",
        names_on_timesheets=tuple(
            name for name in dict.fromkeys([record.name, record.company]) if name
        ),
        email_domains=(),
        quickbooks_customer=record.name,
        invoice_code=setup.invoice_code,
        notes="",
        active=True,
        row_number=0,
    )


def _shared_invoice_codes(clients: dict[str, Client]) -> list[tuple[str, str]]:
    """(category, what to say) for every client sharing its code with another.

    Both are left out rather than either: two clients under one code would give
    their invoices the same numbers, and nothing says which one is right."""
    by_code: dict[str, list[str]] = {}
    for category, client in clients.items():
        by_code.setdefault(client.invoice_code, []).append(category)
    shared: list[tuple[str, str]] = []
    for code, categories in by_code.items():
        if len(categories) < 2:
            continue
        names = " and ".join(clients[category].quickbooks_customer for category in categories)
        said = f"customers {names} both have Invoice code {code}; each client needs its own"
        shared += [(category, said) for category in categories]
    return shared


def _engagement(
    engagement: AccountingEngagement, who: str, paid: _Party | None
) -> tuple[Engagement, Vendor | None, tuple[str, ...]] | list[ListRowProblem]:
    """The engagement and who Icon pays for it -- or, for a salaried employee,
    no vendor and the addresses on the product's `Email:` line."""
    setup = product_setup(engagement.notes)
    problems = [f"{who}: {said} (in its Description on purchase forms)" for said in setup.problems]
    if engagement.bill_rate_cents is None:
        problems.append(f"{who}: no sales price, which is what the client is billed")
    # QuickBooks may hold a cost left blank as zero once the purchase box is
    # ticked to write the `Start:` line; no hourly cost is the same answer.
    if paid is None and not engagement.pay_rate_cents:
        # Nothing on the purchase side at all: an Icon employee on a salary,
        # paid by Kevin each month rather than by the hour (decision 69).
        if problems:
            return [_problem(said) for said in problems]
        assert setup.start is not None and engagement.bill_rate_cents is not None
        return _row(engagement, setup, None), None, setup.emails
    if setup.emails:
        problems.append(
            f'{who}: an "Email:" line is only for a salaried employee\'s product, which has'
            " no vendor; this one is paid through its vendor, whose email is the address"
            " timesheets come from. Remove the line."
        )
    if paid is None:
        problems.append(
            f"{who}: a cost on its purchase side but no preferred vendor, so I do not know"
            " who Icon pays. Add the vendor, or, for an employee on a salary, clear the"
            " cost too."
        )
    if engagement.pay_rate_cents is None:
        problems.append(f"{who}: no cost on its purchase side, which is what Icon pays")
    record = paid.record if paid is not None else None
    if paid is None:
        pass  # said above
    elif record is None:
        problems.append(f"{who}: its vendor could not be read ({paid.failure or 'not found'})")
    elif record.payment_terms_days is None:
        problems.append(
            f"vendor {record.name}: no payment terms, so I cannot say when Icon pays them"
            f" ({record.terms_note or 'none on the record'})"
        )
    if problems:
        return [_problem(said) for said in problems]
    assert record is not None and record.payment_terms_days is not None
    assert setup.start is not None
    assert engagement.bill_rate_cents is not None and engagement.pay_rate_cents is not None
    # The vendor's email is the address this consultant's timesheets come from.
    vendor = Vendor(
        company=record.company or record.name,
        contact_emails=tuple(split_addresses(record.email)),
        paid_by=DEFAULT_PAID_BY,
        pay_timing_days=record.payment_terms_days,
        active=True,
        row_number=0,
    )
    return _row(engagement, setup, Money(engagement.pay_rate_cents)), vendor, ()


def _row(
    engagement: AccountingEngagement, setup: ProductSetup, pay_rate: Money | None
) -> Engagement:
    assert setup.start is not None and engagement.bill_rate_cents is not None
    return Engagement(
        consultant=engagement.consultant,
        client=engagement.client,
        end_client="",
        role="",
        start_date=setup.start,
        end_date=None,  # making the product inactive is how an engagement ends
        billing_schedule=setup.schedule,
        first_period_start=setup.first_period,
        bill_rate=Money(engagement.bill_rate_cents),
        pay_rate=pay_rate,
        rates_from=setup.start,
        send_automatically=setup.send_automatically,
        active=True,
        row_number=0,
    )


def _salaried_employee(
    existing: Consultant | None, name: str, emails: tuple[str, ...]
) -> Consultant:
    """An Icon employee on a salary, at however many clients (decision 69).

    Their address comes off the product's `Email:` line, since there is no
    vendor to hold it; Kevin pays them himself, so there is no payee and no
    pay timing."""
    if existing is not None:
        emails = tuple(dict.fromkeys(existing.emails + emails))
    return Consultant(
        name=name,
        initials="",
        other_names=(),
        emails=emails,
        type=ConsultantType.EMPLOYEE,
        vendor_company="",
        paid_by=PaidBy.SALARY,
        pay_timing_days=0,
        active=True,
        row_number=0,
    )


def _merged_consultant(existing: Consultant | None, name: str, vendor: Vendor) -> Consultant:
    """One consultant, whatever number of clients they work at.

    The vendor's addresses become the consultant's: a timesheet from one of
    them is this person's, which is what lets the sender decide before the
    name on the page does (checks.match_consultant).
    """
    emails = tuple(vendor.contact_emails)
    if existing is not None:
        emails = tuple(dict.fromkeys(existing.emails + emails))
    return Consultant(
        name=name,
        initials="",
        other_names=(),
        emails=emails,
        type=ConsultantType.VENDOR,
        vendor_company=vendor.company,
        paid_by=vendor.paid_by,
        pay_timing_days=vendor.pay_timing_days,
        active=True,
        row_number=0,
    )


def vendors_without_email(workbook: EngagementWorkbook) -> list[str]:
    """Consultants no timesheet can be recognised from yet: their vendor has no
    email in QuickBooks, or, for a salaried employee, their product has no
    `Email:` line. A to-do, not a failure, while timesheets are forwarded."""
    return sorted(consultant.name for consultant in workbook.consultants if not consultant.emails)


__all__ = ["SHEET", "vendors_without_email", "workbook_from_accounting"]
