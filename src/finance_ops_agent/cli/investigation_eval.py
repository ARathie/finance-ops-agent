"""`fops eval-investigator`: build each stuck situation, investigate, score.

A situation is built into the same fakes the scenarios use -- an in-memory
store and accounting system -- so the read-only tools have real records to
look at, and the problem text is made by the same code a run uses. Replaying
(the default, and CI) scores each case's `recorded.json`. `--live` runs the
Claude investigator over the built situation, overwrites `recorded.json`, and
says what it cost (docs/decisions.md #62).
"""

import argparse
import json
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from finance_ops_agent.adapters.fakes.accounting import FakeAccounting
from finance_ops_agent.adapters.fakes.store import FakeStore
from finance_ops_agent.application.agent_tools import ReadOnlyToolbox
from finance_ops_agent.application.diagnosis import (
    FindingKind,
    Looking,
    check_recorded_invoices,
    explain_invoice_number,
)
from finance_ops_agent.application.investigation import problem_for
from finance_ops_agent.application.investigation_eval import (
    InvestigationCase,
    InvestigationReport,
    InvestigationThresholds,
    Recorded,
    RecordedCall,
    Situation,
    below_investigation_thresholds,
    load_investigation_cases,
    read_recorded,
    score_investigation,
)
from finance_ops_agent.domain import emails
from finance_ops_agent.domain.items import EngagementSnapshot, InvoiceRecord
from finance_ops_agent.domain.statuses import ItemStatus
from finance_ops_agent.ports.accounting import CreatedInvoice, InvoiceLookup

ADMIN = "kevin@icon-technologies.com"
_PATH_TO: dict[ItemStatus, tuple[ItemStatus, ...]] = {
    ItemStatus.RECEIVED: (),
    ItemStatus.NEEDS_REVIEW: (ItemStatus.NEEDS_REVIEW,),
    ItemStatus.READY: (ItemStatus.READY,),
    ItemStatus.WAITING_FOR_APPROVAL: (ItemStatus.READY, ItemStatus.WAITING_FOR_APPROVAL),
    ItemStatus.INVOICE_SENT: (ItemStatus.READY, ItemStatus.INVOICE_SENT),
}
ISSUED = date(2026, 9, 30)


@dataclass(frozen=True)
class Built:
    looking: Looking
    problem: str


def _snapshot(client_code: str, consultant_code: str, payee: str) -> EngagementSnapshot:
    return EngagementSnapshot(
        bill_rate_cents=10_500,
        pay_rate_cents=6_700,
        payment_terms_days=30,
        pay_timing_days=15,
        billing_emails=["ap@client.example"],
        cc_emails=[],
        payee=payee,
        paid_by="bank transfer",
        engagement_row_number=0,
        client_invoice_code=client_code,
        consultant_code=consultant_code,
    )


def build_situation(situation: Situation) -> Built:
    store = FakeStore()
    accounting = FakeAccounting()
    ids: dict[str, int] = {}
    for spec in situation.items:
        item = store.create_item(
            spec.consultant,
            spec.client,
            spec.period(),
            ItemStatus.RECEIVED,
            _snapshot(spec.client_code, spec.consultant_code, spec.consultant),
        )
        # Through the same status changes a run would make, never round them.
        for step in _PATH_TO[spec.status]:
            item = store.change_status(item.id, step, {"why": "eval situation"})
        ids[spec.key] = item.id
        for invoice in spec.invoices:
            store.record_invoice(
                InvoiceRecord(
                    id=0,
                    item_id=item.id,
                    number=invoice.number,
                    external_id=invoice.external_id,
                    amount_cents=1_627_500,
                    issue_date=ISSUED,
                    due_date=ISSUED,
                    pdf_sha256="",
                    status=invoice.status,
                    replaces_number=None,
                )
            )
            if invoice.in_quickbooks:
                accounting.invoices[item.id] = CreatedInvoice(
                    number=invoice.number, external_id=invoice.external_id, pdf=b""
                )
    for held in situation.quickbooks_invoices:
        item_id = ids[held.item] if isinstance(held.item, str) else held.item
        accounting.other_invoices[held.external_id] = InvoiceLookup(
            external_id=held.external_id,
            number=held.number,
            total_cents=held.total_cents,
            balance_cents=held.total_cents,
            customer=held.customer,
            item_id=item_id,
            issued=held.issued,
        )
    looking = Looking(store=store, accounting=accounting)

    review = situation.review
    item_id = ids[review.item]
    item = store.get_item(item_id)
    message = review.message
    if review.as_the_agent_writes == "number_taken":
        # As application/outgoing.py writes it, findings included (decision 61).
        message = (
            f"QuickBooks already has an invoice numbered {review.number}, so I could"
            f" not make the invoice for {item.consultant} at {item.client}"
            f" ({item.period.start} to {item.period.end}). Nothing went to the"
            " client. If that invoice is a leftover, delete it in QuickBooks and"
            ' reply "try again". Or reply with the number to use instead, for'
            f' example "use {review.number}-revised".'
        )
        held_by = [
            finding.what
            for finding in explain_invoice_number(looking, review.number, item_id)
            if finding.needs_attention
        ]
        if held_by:
            message += " What I found: " + " ".join(held_by)
    elif review.as_the_agent_writes == "invoice_missing":
        # As application/paid_check.py writes it.
        finding = next(
            f
            for f in check_recorded_invoices(looking)
            if f.kind is FindingKind.INVOICE_NOT_IN_ACCOUNTING and f.item_id == item_id
        )
        message = f"{finding.what} {finding.next_step}"
    store.open_review(item_id, review.code, message)

    email = emails.needs_review(ADMIN, f"{item.consultant} — {item.client}", [message])
    body = email.body
    if situation.what_i_read:
        first, _, rest = body.partition("\n")
        body = "\n".join([first, "", "What I read:", *situation.what_i_read, rest])
    payload = {**email.payload(), "body": body}
    store.record_outgoing("review_email", "review:eval", item_id, payload)
    [record] = store.outgoing_records()
    reviews = [r for r in store.open_reviews() if r.item_id == item_id]
    return Built(looking=looking, problem=problem_for(store, record, reviews))


def run_investigator_eval(args: argparse.Namespace) -> int:
    cases = load_investigation_cases(args.cases)
    investigator = None
    if args.live:
        from finance_ops_agent.adapters.claude.investigator import ClaudeInvestigator

        try:
            investigator = ClaudeInvestigator(model=os.environ.get("FOPS_MODEL", "claude-opus-5"))
        except Exception as error:
            print(f"The live run needs Claude credentials: {error}")
            print("Set ANTHROPIC_API_KEY. CI replays recorded.json only.")
            return 2

    report = InvestigationReport()
    for case in cases:
        built = build_situation(case.situation)
        case_dir = args.cases / case.name
        if investigator is not None:
            result = investigator.investigate(built.problem, ReadOnlyToolbox(built.looking))
            recorded = Recorded(
                investigation=None if result is None else result.investigation,
                calls=[]
                if result is None
                else [
                    RecordedCall(name=c.name, arguments=c.arguments, ok=c.ok) for c in result.calls
                ],
            )
            (case_dir / "recorded.json").write_text(recorded.model_dump_json(indent=2) + "\n")
        elif not (case_dir / "recorded.json").exists():
            raise SystemExit(f"{case.name} has no recorded.json; run with --live once")
        else:
            recorded = read_recorded(case_dir)
        report.scores.append(score_investigation(case, recorded, built.problem))
    print(report.format())

    if investigator is not None:
        from finance_ops_agent.application.eval_runner import MODEL_PRICES, UsageReport

        print()
        usage = UsageReport(
            usage=investigator.usage,
            cases=len(cases),
            prices=MODEL_PRICES.get(investigator.model_name),
            model=investigator.model_name,
        )
        print(usage.format())
        _stamp(args.thresholds, investigator.model_name, investigator.prompt_version)
        print(
            f"\nRecorded answers are now the model's own ({investigator.model_name},"
            f" {investigator.prompt_version}). Read the misses above before trusting it."
        )

    thresholds = InvestigationThresholds.model_validate_json(args.thresholds.read_text())
    if thresholds.source == "bootstrap":
        print(
            "\nWARNING: these recorded answers were written by hand, so the scores prove the"
            " harness, not the investigator. Run `fops eval-investigator --live` to replace them."
        )
    problems = below_investigation_thresholds(report, thresholds)
    for problem in problems:
        print(f"BELOW THRESHOLD: {problem}")
    return 1 if problems else 0


def _stamp(path: Path, model: str, prompt_version: str) -> None:
    """Record which model and prompt wrote the answers. The floor itself is a
    person's to set after reading the scores."""
    raw = json.loads(path.read_text())
    raw.update(
        {
            "source": "live",
            "model": model,
            "prompt_version": prompt_version,
            "recorded_on": date.today().isoformat(),
        }
    )
    path.write_text(json.dumps(raw, indent=2) + "\n")


def scored_case(case: InvestigationCase, recorded: Recorded) -> list[str]:
    """The misses for one case, for tests."""
    return score_investigation(case, recorded, build_situation(case.situation).problem).misses
