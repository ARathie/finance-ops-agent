"""The doctor's "quickbooks setup" line: could QuickBooks alone describe every
engagement? (decision 53) Quoted in docs/quickbooks-setup.md."""

from datetime import date

from finance_ops_agent.cli.doctor import CheckResult, check_quickbooks_setup
from finance_ops_agent.domain.engagements import (
    Consultant,
    ConsultantType,
    Engagement,
    EngagementWorkbook,
    ListRowProblem,
    PaidBy,
)
from finance_ops_agent.domain.money import Money
from finance_ops_agent.domain.periods import BillingSchedule


def built(
    emails: tuple[str, ...] = ("priya@example.com",), problems: int = 0
) -> EngagementWorkbook:
    return EngagementWorkbook(
        clients=[],
        consultants=[
            Consultant(
                name="Priya Shah",
                initials="",
                other_names=(),
                emails=emails,
                type=ConsultantType.VENDOR,
                vendor_company="Priya Shah",
                paid_by=PaidBy.BANK_TRANSFER,
                pay_timing_days=15,
                active=True,
                row_number=0,
            )
        ],
        vendors=[],
        engagements=[
            Engagement(
                consultant="Priya Shah",
                client="Acme Corp",
                end_client="",
                role="",
                start_date=date(2026, 8, 1),
                end_date=None,
                billing_schedule=BillingSchedule.MONTHLY,
                first_period_start=None,
                bill_rate=Money(14_000),
                pay_rate=Money(10_000),
                rates_from=date(2026, 8, 1),
                send_automatically=False,
                active=True,
                row_number=1,
            )
        ],
        problems=[ListRowProblem("QuickBooks", 0, 'customer Acme Corp: no "Invoice code:" line')]
        * problems,
    )


def test_everything_in_place_passes_and_counts() -> None:
    check = check_quickbooks_setup(lambda: built(), ())
    assert check.result is CheckResult.PASS
    assert check.detail == "QuickBooks alone describes all 1 engagement(s) for 1 client(s)"


def test_a_problem_fails_and_names_the_record() -> None:
    check = check_quickbooks_setup(lambda: built(problems=1), ())
    assert check.result is CheckResult.FAIL
    assert 'customer Acme Corp: no "Invoice code:" line' in check.detail


def test_a_vendor_with_no_email_is_a_to_do_while_timesheets_are_forwarded() -> None:
    check = check_quickbooks_setup(lambda: built(emails=()), ("tester@example.com",))
    assert check.result is CheckResult.PASS
    assert "an email on the vendor for Priya Shah" in check.detail


def test_a_vendor_with_no_email_fails_once_nobody_forwards() -> None:
    check = check_quickbooks_setup(lambda: built(emails=()), ())
    assert check.result is CheckResult.FAIL
    assert "Priya Shah" in check.detail


def test_quickbooks_that_cannot_be_asked_is_reported_not_raised() -> None:
    def refuse() -> EngagementWorkbook:
        raise RuntimeError("the connection has expired")

    check = check_quickbooks_setup(refuse, ())
    assert check.result is CheckResult.FAIL
    assert "expired" in check.detail
