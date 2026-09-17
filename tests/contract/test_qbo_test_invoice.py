"""`fops qbo-test-invoice` against recorded responses.

The command exists because the ordinary flow cannot prove this path: dry run
creates nothing, and the only other way into QuickBooks is Kevin approving an
invoice, which sends the billing email. So these tests cover what it does
instead -- build the item from the engagement list, create, check, fetch the
PDF, and take the invoice back out again.
"""

from datetime import date
from pathlib import Path

import pytest

from finance_ops_agent.cli.qbo_test import InvoiceTestFailed, build_test_item, create_and_check
from finance_ops_agent.domain.engagements import EngagementWorkbook, parse_workbook
from finance_ops_agent.domain.items import Item
from finance_ops_agent.domain.money import Hours, Money
from tests.contract.test_quickbooks import build, replay_from
from tests.scenarios.conftest import default_workbook

AUG_31 = date(2026, 8, 31)
HOURS = Hours(15_600)  # 156.00


def workbook() -> EngagementWorkbook:
    return parse_workbook(default_workbook())


def item(item_id: int = 1) -> Item:
    return build_test_item(workbook(), "Priya Shah", "Acme Corp", AUG_31, HOURS, item_id)


class TestBuildingTheItem:
    def test_everything_comes_off_the_engagement_list(self) -> None:
        built = item()
        assert built.consultant == "Priya Shah"
        assert built.client == "Acme Corp"
        assert built.period.start == date(2026, 8, 1)
        assert built.period.end == AUG_31
        assert built.invoice_amount == Money(2_184_000)  # 156.00 at 140.00
        assert built.snapshot.client_invoice_code == "AC"
        assert built.snapshot.consultant_code == "PS"

    def test_the_period_comes_from_the_engagement_schedule(self) -> None:
        """Any day in the period will do; the schedule decides the rest."""
        built = build_test_item(workbook(), "Priya Shah", "Acme Corp", date(2026, 8, 12), HOURS, 1)
        assert (built.period.start, built.period.end) == (date(2026, 8, 1), AUG_31)

    def test_a_consultant_who_is_not_on_the_list_says_who_is(self) -> None:
        """The failure path: nothing is invented for a pairing Kevin never set up."""
        with pytest.raises(InvoiceTestFailed) as error:
            build_test_item(workbook(), "Nobody At All", "Acme Corp", AUG_31, HOURS, 1)
        assert "no row for Nobody At All at Acme Corp" in str(error.value)
        assert "Priya Shah at Acme Corp" in str(error.value)  # what it does have


class TestAgainstQuickBooks:
    def test_it_creates_checks_and_then_removes_the_invoice(self, tmp_path: Path) -> None:
        replay = replay_from("test_invoice_ok")
        accounting, _, _ = build(replay, tmp_path)

        quickbooks_id, pdf = create_and_check(accounting, item(), date(2026, 9, 3), lambda _: None)

        assert quickbooks_id == "145"
        assert pdf.startswith(b"%PDF")
        body = next(e for e in replay.bodies if isinstance(e, dict) and "Line" in e)
        assert body["DocNumber"] == "083126AC-PS"

        accounting.delete_invoice(quickbooks_id)
        assert any("operation=delete" in url for _, url in replay.calls)
        assert replay.bodies[-1] == {"Id": "145", "SyncToken": "0"}

    def test_a_number_quickbooks_already_has_steps_to_the_next_one(self, tmp_path: Path) -> None:
        """A test invoice that was voided rather than deleted keeps its number,
        so the next run has to step past it exactly as a replacement does."""
        replay = replay_from("test_invoice_duplicate_number")
        accounting, _, _ = build(replay, tmp_path)

        said: list[str] = []
        quickbooks_id, _ = create_and_check(accounting, item(), date(2026, 9, 3), said.append)

        assert quickbooks_id == "146"
        numbers = [e["DocNumber"] for e in replay.bodies if isinstance(e, dict) and "Line" in e]
        assert numbers == ["083126AC-PS", "083126AC-PS-2"]
        assert any("already has 083126AC-PS" in line for line in said)
