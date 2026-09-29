"""QuickBooks says which engagements are live; the workbook says when to bill.

Every case here is a disagreement between the two, because agreement is the
easy half. What matters is that a disagreement is never resolved by guessing:
it is either a review for Kevin or a decision written down in a decision.
"""

from datetime import date

from finance_ops_agent.domain.engagements import (
    Client,
    Delivery,
    Engagement,
    EngagementWorkbook,
)
from finance_ops_agent.domain.live_engagements import live_engagements
from finance_ops_agent.domain.money import Money
from finance_ops_agent.domain.periods import BillingSchedule


def client(name: str, legal_name: str = "", quickbooks_customer: str = "") -> Client:
    return Client(
        name=name,
        legal_name=legal_name or name,
        billing_contact="Accounts Payable",
        billing_emails=("ap@example.com",),
        cc_emails=(),
        payment_terms_days=30,
        delivery=Delivery.EMAIL,
        time_system="",
        names_on_timesheets=(),
        email_domains=(),
        quickbooks_customer=quickbooks_customer,
        invoice_code="XX",
        notes="",
        active=True,
        row_number=2,
    )


def engagement(consultant: str, client_name: str, *, active: bool = True) -> Engagement:
    return Engagement(
        consultant=consultant,
        client=client_name,
        end_client="",
        role="Developer",
        start_date=date(2026, 8, 1),
        end_date=None,
        billing_schedule=BillingSchedule.MONTHLY,
        first_period_start=None,
        bill_rate=Money(14000),
        pay_rate=Money(10000),
        rates_from=date(2026, 8, 1),
        send_automatically=False,
        active=active,
        row_number=2,
    )


def workbook(*rows: Engagement, clients: list[Client] | None = None) -> EngagementWorkbook:
    return EngagementWorkbook(
        clients=clients if clients is not None else [client("Acme Corp")],
        consultants=[],
        vendors=[],
        engagements=list(rows),
        problems=[],
    )


class TestWhenTheAccountingSystemHasNothingToSay:
    """Manual mode, or a company whose products are not filled in yet."""

    def test_the_engagement_list_decides_on_its_own(self) -> None:
        book = workbook(
            engagement("Priya Shah", "Acme Corp"),
            engagement("Dana Cruz", "Acme Corp", active=False),
        )
        answer = live_engagements(book, [])
        assert answer.live == [("Priya Shah", "Acme Corp")]
        assert answer.without_a_row == []
        assert answer.finished == []

    def test_an_empty_list_never_reads_as_everything_having_finished(self) -> None:
        """The dangerous reading: silence must not stop the agent billing."""
        book = workbook(engagement("Priya Shah", "Acme Corp"))
        assert live_engagements(book, []).finished == []


class TestWhenQuickBooksDecides:
    def test_a_product_that_is_gone_means_no_new_periods(self) -> None:
        book = workbook(
            engagement("Priya Shah", "Acme Corp"),
            engagement("Dana Cruz", "Acme Corp"),
        )
        answer = live_engagements(book, [("Priya Shah", "Acme Corp")])
        assert answer.live == [("Priya Shah", "Acme Corp")]
        assert answer.finished == [("Dana Cruz", "Acme Corp")]

    def test_a_live_product_overrides_a_row_marked_inactive_by_hand(self) -> None:
        """QuickBooks is the switch now, so the row's own flag does not veto it
        -- and the schedule is still read off that row."""
        book = workbook(engagement("Priya Shah", "Acme Corp", active=False))
        answer = live_engagements(book, [("Priya Shah", "Acme Corp")])
        assert answer.live == [("Priya Shah", "Acme Corp")]
        assert answer.without_a_row == []

    def test_an_engagement_with_no_row_is_named_rather_than_guessed_at(self) -> None:
        """No row means no billing schedule and no start date, which no
        accounting system holds."""
        book = workbook(engagement("Priya Shah", "Acme Corp"))
        answer = live_engagements(book, [("Priya Shah", "Acme Corp"), ("Sam Okafor", "Acme Corp")])
        assert answer.live == [("Priya Shah", "Acme Corp")]
        assert answer.without_a_row == ["Sam Okafor at Acme Corp"]

    def test_a_client_under_a_different_name_is_the_same_client(self) -> None:
        """The category in QuickBooks is often the legal name, and the list's
        own short name is what its rows use."""
        book = workbook(
            engagement("Priya Shah", "Acme Corp"),
            clients=[client("Acme Corp", legal_name="Acme Corporation")],
        )
        answer = live_engagements(book, [("Priya Shah", "Acme Corporation")])
        assert answer.live == [("Priya Shah", "Acme Corp")]
        assert answer.without_a_row == []

    def test_the_quickbooks_customer_name_counts_too(self) -> None:
        book = workbook(
            engagement("Priya Shah", "Acme Corp"),
            clients=[client("Acme Corp", quickbooks_customer="Acme (Atlanta)")],
        )
        assert live_engagements(book, [("Priya Shah", "Acme (Atlanta)")]).live == [
            ("Priya Shah", "Acme Corp")
        ]

    def test_a_consultant_at_two_clients_is_two_engagements(self) -> None:
        book = workbook(
            engagement("Priya Shah", "Acme Corp"),
            engagement("Priya Shah", "MasTec"),
            clients=[client("Acme Corp"), client("MasTec")],
        )
        answer = live_engagements(book, [("Priya Shah", "MasTec")])
        assert answer.live == [("Priya Shah", "MasTec")]
        assert answer.finished == [("Priya Shah", "Acme Corp")]

    def test_the_workbook_order_is_kept(self) -> None:
        """Items are created in this order and their numbers are what Kevin
        reads, so they follow his rows rather than the alphabet."""
        book = workbook(
            engagement("Zoe Adams", "Acme Corp"),
            engagement("Al Brown", "Acme Corp"),
        )
        answer = live_engagements(book, [("Al Brown", "Acme Corp"), ("Zoe Adams", "Acme Corp")])
        assert answer.live == [("Zoe Adams", "Acme Corp"), ("Al Brown", "Acme Corp")]

    def test_several_rate_rows_for_one_engagement_are_one_engagement(self) -> None:
        book = workbook(
            engagement("Priya Shah", "Acme Corp"),
            engagement("Priya Shah", "Acme Corp"),
        )
        assert live_engagements(book, [("Priya Shah", "Acme Corp")]).live == [
            ("Priya Shah", "Acme Corp")
        ]
