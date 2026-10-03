"""Reading Kevin's setup form: code only, strict, and every problem named
(decision 56)."""

from datetime import date

from finance_ops_agent.domain import setup
from finance_ops_agent.domain.engagements import EngagementWorkbook, parse_workbook
from finance_ops_agent.domain.money import Money
from tests.scenarios.conftest import default_workbook


def workbook() -> EngagementWorkbook:
    return parse_workbook(default_workbook())  # Acme Corp (AC), Priya and Dana there


def reply(**fields: str) -> str:
    values = {
        setup.CONSULTANT: "Sam Okafor",
        setup.CONSULTANT_EMAIL: "sam@example.com",
        setup.FIRM: "",
        setup.PAY_WITHIN: "15",
        setup.CLIENT: "Acme Corp",
        setup.BILL_RATE: "140.00",
        setup.PAY_RATE: "100.00",
        setup.START: "2026-09-01",
    }
    values.update(fields)
    return "Here you go.\n\n" + "\n".join(f"{label}: {value}" for label, value in values.items())


class TestTheForm:
    def test_it_shows_every_label_with_what_is_known_and_hints_for_the_rest(self) -> None:
        lines = setup.form({setup.CONSULTANT_EMAIL: "sam@example.com"})
        assert lines[1] == "Consultant email: sam@example.com"
        assert [line.split(":")[0] for line in lines] == list(setup.LABELS)
        assert any("new client only" in line for line in lines)

    def test_a_form_sent_straight_back_reads_as_what_was_typed(self) -> None:
        """Kevin copies the lines with their hints in brackets: the hint is not
        part of the value."""
        text = "\n".join(setup.form({setup.CONSULTANT: "Sam Okafor"}))
        fields = setup.filled_in(text)
        assert fields[setup.CONSULTANT] == "Sam Okafor"
        assert fields[setup.FIRM] == ""


class TestKevinsOwnWords:
    def test_the_quoted_email_below_his_reply_is_not_read(self) -> None:
        text = "Bill rate: 150\n\nOn Tue, Sep 8, 2026, Jay wrote:\nBill rate: 999\nPay rate: 999\n"
        assert setup.filled_in(text) == {setup.BILL_RATE: "150"}

    def test_quoted_lines_are_skipped(self) -> None:
        assert setup.filled_in("> Pay rate: 1\nPay rate: 100") == {setup.PAY_RATE: "100"}

    def test_only_a_reply_with_both_rates_is_a_setup(self) -> None:
        assert setup.looks_like_a_setup(reply())
        assert not setup.looks_like_a_setup("try again")
        assert not setup.looks_like_a_setup("Bill rate: 140")


class TestReadingIt:
    def test_a_new_consultant_at_an_existing_client(self) -> None:
        plan, problems, _ = setup.read_setup(reply(), workbook())

        assert problems == []
        assert plan is not None
        assert plan.client == "Acme Corp"
        assert not plan.new_client
        assert plan.bill_rate == Money(14_000)
        assert plan.pay_rate == Money(10_000)
        assert plan.start == date(2026, 9, 1)
        assert plan.pay_within_days == 15

    def test_a_new_client_needs_its_billing_details(self) -> None:
        plan, problems, _ = setup.read_setup(reply(**{setup.CLIENT: "Globex"}), workbook())

        assert plan is None
        assert '"Client billing email" is blank.' in problems
        assert '"Invoice code" is blank.' in problems
        assert any("Client pays within" in problem for problem in problems)

    def test_a_new_client_with_them(self) -> None:
        plan, problems, _ = setup.read_setup(
            reply(
                **{
                    setup.CLIENT: "Globex",
                    setup.CLIENT_EMAIL: "ap@globex.example",
                    setup.CLIENT_PAYS_WITHIN: "Net 45",
                    setup.INVOICE_CODE: "gx",
                }
            ),
            workbook(),
        )
        assert problems == []
        assert plan is not None and plan.new_client
        assert (plan.invoice_code, plan.client_pays_within_days) == ("GX", 45)
        assert plan.client_legal_name == "Globex"

    def test_rates_written_the_way_people_write_them(self) -> None:
        plan, _, _ = setup.read_setup(
            reply(**{setup.BILL_RATE: "$1,140.50/hr", setup.PAY_RATE: "100 per hour"}),
            workbook(),
        )
        assert plan is not None
        assert plan.bill_rate == Money(114_050)
        assert plan.pay_rate == Money(10_000)

    def test_nothing_is_guessed(self) -> None:
        plan, problems, given = setup.read_setup(
            reply(
                **{
                    setup.CONSULTANT_EMAIL: "not an address",
                    setup.BILL_RATE: "about 140",
                    setup.PAY_RATE: "0",
                    setup.START: "next Monday",
                    setup.PAY_WITHIN: "soon",
                }
            ),
            workbook(),
        )
        assert plan is None
        assert len(problems) == 5
        assert given[setup.BILL_RATE] == "about 140"  # shown back to him as he wrote it

    def test_an_invoice_code_another_client_has(self) -> None:
        _, problems, _ = setup.read_setup(
            reply(
                **{
                    setup.CLIENT: "Globex",
                    setup.CLIENT_EMAIL: "ap@globex.example",
                    setup.CLIENT_PAYS_WITHIN: "30",
                    setup.INVOICE_CODE: "AC",
                }
            ),
            workbook(),
        )
        assert "The invoice code AC is already Acme Corp's." in problems

    def test_an_engagement_that_already_exists(self) -> None:
        _, problems, _ = setup.read_setup(reply(**{setup.CONSULTANT: "Priya Shah"}), workbook())
        assert any("already has an engagement" in problem for problem in problems)

    def test_paying_more_than_charging_is_said_out_loud(self) -> None:
        plan, _, _ = setup.read_setup(reply(**{setup.PAY_RATE: "150"}), workbook())
        assert plan is not None
        assert any("pay more" in line for line in plan.summary_lines())

    def test_it_survives_being_kept(self) -> None:
        plan, _, _ = setup.read_setup(reply(), workbook())
        assert plan is not None
        assert setup.EngagementSetup.from_dict(plan.to_dict()) == plan
