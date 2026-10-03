"""`fops diagnose` prints problems first, each with what to do."""

import argparse

from finance_ops_agent.adapters.fakes.accounting import FakeAccounting
from finance_ops_agent.adapters.fakes.store import FakeStore
from finance_ops_agent.application.diagnosis import Finding, FindingKind, Looking
from finance_ops_agent.cli.diagnose import render_findings, run_diagnosis


def test_problems_come_first_with_what_to_do() -> None:
    lines = render_findings(
        [
            Finding(
                FindingKind.EMAIL_WILL_BE_READ, "an email will be read", "", needs_attention=False
            ),
            Finding(FindingKind.INVOICE_NOT_IN_ACCOUNTING, "invoice 153 is gone", "forget item 6"),
        ]
    )
    assert lines[0] == "PROBLEM  invoice 153 is gone"
    assert lines[1] == "         What to do: forget item 6"
    assert lines[-1] == "ok       an email will be read"


def test_nothing_stuck_says_so() -> None:
    assert render_findings([]) == ["Nothing looks stuck."]


def test_an_unknown_item_is_refused_plainly() -> None:
    said: list[str] = []
    args = argparse.Namespace(item=99, number="", no_mail=True)
    looking = Looking(store=FakeStore(), accounting=FakeAccounting())

    assert run_diagnosis(args, looking, lambda: None, said.append) == 1
    assert said == ["There is no item 99. Run `fops forget` to list them."]
