"""The starter workbook Kevin fills in (roadmap PR 13).

It is committed as a binary, so the risk is that a column changes in the
reader and the workbook quietly stops matching. These load the committed file
through the real reader and check it produces no problems at all, so the first
thing Kevin ever does with the agent cannot be reading an error message.
"""

from datetime import date
from pathlib import Path

import pytest

from finance_ops_agent.adapters.excel.engagement_list import ExcelEngagementList
from finance_ops_agent.domain.engagements import (
    ConsultantType,
    Delivery,
    EngagementWorkbook,
    PaidBy,
    parse_workbook,
)
from finance_ops_agent.domain.money import Money
from finance_ops_agent.domain.periods import BillingSchedule

TEMPLATE = Path(__file__).resolve().parents[2] / "templates" / "engagements-template.xlsx"


@pytest.fixture(scope="module")
def workbook() -> EngagementWorkbook:
    return parse_workbook(ExcelEngagementList(TEMPLATE).load())


def test_the_template_is_committed() -> None:
    assert TEMPLATE.is_file(), "run: uv run python templates/build_template.py"


def test_it_loads_with_no_problems_at_all(workbook: EngagementWorkbook) -> None:
    assert [str(problem) for problem in workbook.problems] == []


def test_every_sheet_has_its_one_example_row(workbook: EngagementWorkbook) -> None:
    assert len(workbook.clients) == 1
    assert len(workbook.consultants) == 1
    assert len(workbook.vendors) == 1
    assert len(workbook.engagements) == 1


def test_the_example_row_is_the_worked_example(workbook: EngagementWorkbook) -> None:
    engagement = workbook.engagements[0]
    assert engagement.consultant == "Priya Shah"
    assert engagement.client == "Acme Corp"
    assert engagement.billing_schedule is BillingSchedule.MONTHLY
    # The two rates the whole agent turns on, and they are not the same number.
    assert engagement.bill_rate == Money(14_000)
    assert engagement.pay_rate == Money(10_000)
    assert engagement.start_date == date(2026, 2, 1)
    assert engagement.rates_from == date(2026, 2, 1)
    assert engagement.send_automatically is False


def test_the_client_and_consultant_are_usable(workbook: EngagementWorkbook) -> None:
    client = workbook.clients[0]
    assert client.delivery is Delivery.EMAIL
    assert client.billing_emails == ("ap@acme.example",)
    assert client.payment_terms_days == 30
    assert client.active

    consultant = workbook.consultants[0]
    assert consultant.type is ConsultantType.CONTRACTOR
    assert consultant.paid_by is PaidBy.BANK_TRANSFER
    assert consultant.pay_timing_days == 15
    assert consultant.active


def test_the_names_join_up_across_the_sheets(workbook: EngagementWorkbook) -> None:
    """A row that names a consultant or client the other sheets do not have is
    the mistake this template exists to prevent."""
    engagement = workbook.engagements[0]
    assert engagement.consultant in {c.name for c in workbook.consultants}
    assert engagement.client in {c.name for c in workbook.clients}


def test_the_committed_file_matches_the_builder() -> None:
    """If a column is added to the reader, the builder is the place to change
    it; this fails when the committed workbook was not rebuilt afterwards."""
    from templates.build_template import SHEETS

    raw = ExcelEngagementList(TEMPLATE).load()
    for name, rows in (
        ("Clients", raw.clients),
        ("Consultants", raw.consultants),
        ("Vendors", raw.vendors),
        ("Engagements", raw.engagements),
    ):
        expected, _ = SHEETS[name]
        assert list(rows[0].cells) == expected, f"{name} columns differ from build_template.py"
