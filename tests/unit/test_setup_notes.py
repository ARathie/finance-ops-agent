"""The `Label: value` lines Kevin writes in QuickBooks (decision 53)."""

from datetime import date

from finance_ops_agent.domain.engagements import Delivery
from finance_ops_agent.domain.periods import BillingSchedule
from finance_ops_agent.domain.setup_notes import (
    CUSTOMER_LABELS,
    customer_setup,
    labelled_lines,
    product_setup,
)


class TestReadingTheLines:
    def test_labels_ignore_case_spacing_and_kevins_own_lines(self) -> None:
        values, problems = labelled_lines(
            "AP want the PO number on everything.\n  invoice   CODE :  MT \nContact: Bob",
            CUSTOMER_LABELS,
        )
        assert values == {"invoice code": "MT"}
        assert problems == []

    def test_a_pasted_non_breaking_space_counts_as_a_space(self) -> None:
        values, _ = labelled_lines("Invoice\xa0code: MT", CUSTOMER_LABELS)
        assert values == {"invoice code": "MT"}

    def test_the_same_label_twice_with_different_values_is_refused(self) -> None:
        _, problems = labelled_lines("Invoice code: MT\nInvoice code: MA", CUSTOMER_LABELS)
        assert problems and "written twice" in problems[0]

    def test_the_same_label_twice_with_the_same_value_is_fine(self) -> None:
        _, problems = labelled_lines("Invoice code: MT\ninvoice code: mt", CUSTOMER_LABELS)
        assert problems == []


class TestACustomer:
    def test_the_invoice_code_and_the_defaults(self) -> None:
        setup = customer_setup("Invoice code: mt")
        assert setup.invoice_code == "MT"
        assert setup.delivery is Delivery.EMAIL
        assert setup.cc_emails == ()
        assert setup.problems == []

    def test_no_invoice_code_is_a_problem(self) -> None:
        assert "Invoice code" in customer_setup("").problems[0]

    def test_an_invoice_code_that_is_not_two_letters_is_a_problem_not_a_guess(self) -> None:
        setup = customer_setup("Invoice code: MTC")
        assert setup.invoice_code == ""
        assert "two letters" in setup.problems[0]

    def test_portal_and_cc_when_written(self) -> None:
        setup = customer_setup(
            "Invoice code: MT\nDelivery: Portal\nCC: jane@mastec.example, bob@mastec.example"
        )
        assert setup.delivery is Delivery.PORTAL
        assert setup.cc_emails == ("jane@mastec.example", "bob@mastec.example")
        assert setup.problems == []

    def test_an_unreadable_delivery_or_cc_is_a_problem(self) -> None:
        setup = customer_setup("Invoice code: MT\nDelivery: fax\nCC: Jane at MasTec")
        assert len(setup.problems) == 2
        assert setup.cc_emails == ()


class TestAProduct:
    def test_a_start_date_and_the_defaults(self) -> None:
        setup = product_setup("Start: 2026-02-01\nContract in the shared drive")
        assert setup.start == date(2026, 2, 1)
        assert setup.schedule is BillingSchedule.MONTHLY
        assert setup.send_automatically is False
        assert setup.problems == []

    def test_an_american_date_is_read_too(self) -> None:
        assert product_setup("Start: 2/1/2026").start == date(2026, 2, 1)

    def test_no_start_is_a_problem(self) -> None:
        setup = product_setup("")
        assert setup.start is None
        assert "Start:" in setup.problems[0]

    def test_a_start_that_is_not_a_date_is_a_problem(self) -> None:
        setup = product_setup("Start: February")
        assert setup.start is None
        assert "should be a date" in setup.problems[0]

    def test_a_weekly_schedule_needs_its_first_period(self) -> None:
        setup = product_setup("Start: 2026-02-01\nSchedule: weekly")
        assert "First period" in setup.problems[0]

    def test_a_weekly_schedule_with_its_first_period(self) -> None:
        setup = product_setup("Start: 2026-02-01\nSchedule: Weekly\nFirst period: 2026-02-02")
        assert setup.schedule is BillingSchedule.WEEKLY
        assert setup.first_period == date(2026, 2, 2)
        assert setup.problems == []

    def test_an_unknown_schedule_is_a_problem(self) -> None:
        assert "Schedule" in product_setup("Start: 2026-02-01\nSchedule: fortnightly").problems[0]

    def test_send_automatically(self) -> None:
        assert product_setup("Start: 2026-02-01\nSend automatically: yes").send_automatically
        assert product_setup("Start: 2026-02-01\nSend automatically: sure").problems
