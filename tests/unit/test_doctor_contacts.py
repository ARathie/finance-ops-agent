"""The contacts check: does QuickBooks agree with the engagement list?

Nothing uses QuickBooks' answer yet. This is the comparison that has to be
clean before the timesheet addresses and the payment terms move across
(docs/decisions.md #45), the same shape decision 37 used before the pay rate
moved.
"""

from finance_ops_agent.cli.doctor import (
    Check,
    CheckResult,
    ExpectedParty,
    check_quickbooks_contacts,
)
from finance_ops_agent.ports.accounting import AccountingParty


class FakeCompany:
    """Stands in for the QuickBooks adapter; the check only asks it two things."""

    company = "the sandbox company 913"

    def __init__(
        self,
        customers: dict[str, AccountingParty] | None = None,
        payees: dict[str, AccountingParty] | None = None,
        raises: Exception | None = None,
    ) -> None:
        self._customers = customers or {}
        self._payees = payees or {}
        self._raises = raises

    def customer(self, name: str) -> AccountingParty | None:
        if self._raises:
            raise self._raises
        return self._customers.get(name)

    def payee(self, ref: str) -> AccountingParty | None:
        if self._raises:
            raise self._raises
        return self._payees.get(ref)


def held(email: str = "ap@acme.example", days: int | None = 30) -> AccountingParty:
    return AccountingParty(ref="58", name="Acme Corporation", email=email, payment_terms_days=days)


def client(emails: list[str] | None = None, days: int = 30) -> ExpectedParty:
    return ExpectedParty(
        what="client",
        name="Acme Corp",
        lookup="Acme Corporation",
        emails=["ap@acme.example"] if emails is None else emails,
        payment_terms_days=days,
    )


def check(company: FakeCompany, *parties: ExpectedParty) -> Check:
    return check_quickbooks_contacts(company, lambda: list(parties))


def test_agreement_passes_and_counts_what_it_compared() -> None:
    result = check(FakeCompany({"Acme Corporation": held()}), client())
    assert result.result is CheckResult.PASS
    assert "1 record(s)" in result.detail


def test_a_different_email_fails_and_names_both() -> None:
    result = check(FakeCompany({"Acme Corporation": held(email="billing@acme.example")}), client())
    assert result.result is CheckResult.FAIL
    assert "billing@acme.example" in result.detail  # QuickBooks
    assert "ap@acme.example" in result.detail  # the engagement list


def test_one_of_several_listed_addresses_is_agreement() -> None:
    """The list holds every address a client sends invoices to; QuickBooks
    holds one. Matching any of them is agreement, not a difference."""
    result = check(
        FakeCompany({"Acme Corporation": held(email="ap@acme.example")}),
        client(emails=["accounts@acme.example", "ap@acme.example"]),
    )
    assert result.result is CheckResult.PASS


def test_the_comparison_ignores_case() -> None:
    result = check(FakeCompany({"Acme Corporation": held(email="AP@Acme.Example")}), client())
    assert result.result is CheckResult.PASS


def test_different_terms_fail_and_name_both() -> None:
    result = check(FakeCompany({"Acme Corporation": held(days=45)}), client(days=30))
    assert result.result is CheckResult.FAIL
    assert "45 day(s)" in result.detail
    assert "says 30" in result.detail


def test_a_blank_field_is_not_a_disagreement() -> None:
    """It has not been filled in, and the check says which so Kevin knows what
    is left rather than being told he is wrong."""
    result = check(FakeCompany({"Acme Corporation": held(email="", days=None)}), client())
    assert result.result is CheckResult.PASS
    assert "not filled in yet" in result.detail
    assert "no email" in result.detail
    assert "no payment terms" in result.detail


def test_no_record_at_all_is_named_rather_than_counted_as_agreement() -> None:
    result = check(FakeCompany(), client())
    assert result.result is CheckResult.PASS
    assert "no record in QuickBooks" in result.detail


def test_a_payee_with_nothing_to_look_up_is_named_not_skipped() -> None:
    """A company with no purchase sides filled in must not read the same as one
    that agrees about everything."""
    payee = ExpectedParty(
        what="payee",
        name="Priya Shah",
        lookup="",
        emails=["priya@example.com"],
        payment_terms_days=15,
    )
    result = check(FakeCompany(), payee)
    assert result.result is CheckResult.PASS
    assert "Priya Shah (no vendor on its product)" in result.detail


def test_nothing_to_compare_says_so() -> None:
    assert check(FakeCompany()).detail.startswith("nothing to compare")


def test_nothing_compared_still_says_what_is_missing() -> None:
    """Otherwise the one case where every record is absent is the one that
    reports the least."""
    result = check(FakeCompany(), client())
    assert "no record in QuickBooks" in result.detail


def test_a_lookup_that_fails_is_reported_rather_than_swallowed() -> None:
    result = check(FakeCompany(raises=RuntimeError("two customers share that name")), client())
    assert result.result is CheckResult.FAIL
    assert "two customers share that name" in result.detail


def test_a_payee_is_looked_up_by_id_not_by_name() -> None:
    """A vendor filed under a spelling nobody expected is still the one
    compared."""
    payee = ExpectedParty(
        what="payee",
        name="Priya Shah",
        lookup="7",
        emails=["priya@example.com"],
        payment_terms_days=15,
    )
    company = FakeCompany(
        payees={
            "7": AccountingParty(
                ref="7", name="P. Shah", email="priya@example.com", payment_terms_days=15
            )
        }
    )
    assert check(company, payee).result is CheckResult.PASS
