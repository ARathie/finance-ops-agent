"""`fops diagnose`: what is stuck and why, read-only (docs/decisions.md #55).

Wires the real store, accounting system and inbox into the read-only views and
prints what `application/diagnosis.py` finds. It changes nothing: not the
records, not QuickBooks, not the mailbox position, not a read flag.
"""

import argparse
from collections.abc import Callable

from finance_ops_agent.application.diagnosis import (
    Finding,
    ItemReport,
    Looking,
    describe_item,
    diagnose_everything,
    explain_invoice_number,
)
from finance_ops_agent.domain.engagements import EngagementWorkbook


def render_findings(findings: list[Finding]) -> list[str]:
    """Problems first, then what is fine, each with what to do."""
    if not findings:
        return ["Nothing looks stuck."]
    lines: list[str] = []
    problems = [finding for finding in findings if finding.needs_attention]
    fine = [finding for finding in findings if not finding.needs_attention]
    for finding in problems:
        lines.append(f"PROBLEM  {finding.what}")
        if finding.next_step:
            lines.append(f"         What to do: {finding.next_step}")
        lines.append("")
    for finding in fine:
        lines.append(f"ok       {finding.what}")
    if not problems:
        lines.insert(0, "Nothing looks stuck.")
    return lines


def render_item(report: ItemReport) -> list[str]:
    lines = [f"Item {report.item.id}", *[f"  {fact}" for fact in report.facts], ""]
    return lines + render_findings(report.findings)


def run_diagnosis(
    args: argparse.Namespace,
    looking: Looking,
    workbook: Callable[[], EngagementWorkbook | None],
    say: Callable[[str], None] = print,
) -> int:
    if args.item is not None:
        known = {item.id for item in looking.store.list_items()}
        if args.item not in known:
            say(f"There is no item {args.item}. Run `fops forget` to list them.")
            return 1
        lines = render_item(describe_item(looking, args.item))
    elif args.number:
        lines = render_findings(explain_invoice_number(looking, args.number))
    else:
        lines = render_findings(diagnose_everything(looking, workbook()))
    for line in lines:
        say(line)
    return 0
