"""`fops eval-investigator`: build each stuck situation, investigate, score.

A situation is built into the same fakes the scenarios use -- an in-memory
store and accounting system -- so the read-only tools have real records to
look at, and the problem text is made by the same code a run uses. Replaying
(the default, and CI) scores each case's `recorded.json`. `--live` runs the
Claude investigator over the built situation, overwrites `recorded.json`, and
says what it cost (docs/decisions.md #62).
"""

import argparse
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import date, datetime
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
from finance_ops_agent.application.investigation import forms_for, problem_for
from finance_ops_agent.application.investigation_eval import (
    InvestigationCase,
    InvestigationReport,
    InvestigationThresholds,
    Recorded,
    RecordedCall,
    Situation,
    below_investigation_thresholds,
    has_recorded,
    load_investigation_cases,
    read_recorded,
    score_investigation,
)
from finance_ops_agent.domain import emails
from finance_ops_agent.domain.engagements import RawRow, RawWorkbook, parse_workbook
from finance_ops_agent.domain.investigation import ReplyForm
from finance_ops_agent.domain.items import (
    EngagementSnapshot,
    InvoiceRecord,
    OutgoingRecord,
    ReviewRecord,
    TimesheetRecord,
)
from finance_ops_agent.domain.messages import MessageKind, StoredAttachment, StoredMessage
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
    forms: list[ReplyForm]
    seen: str  # everything the tools could show, for the safety check


# Complete rows for each sheet, as the engagement list template has them; a
# case's rows are merged over these, so it names only what matters to it.
_DEFAULT_ROWS: dict[str, dict[str, str]] = {
    "clients": {
        "Client": "Acme Corp",
        "Legal name": "Acme Corporation",
        "Billing contact": "Accounts Payable",
        "Billing email": "ap@acme.example",
        "CC email": "",
        "Payment terms (days)": "30",
        "Delivery": "email",
        "Time system": "",
        "Names on timesheets": "Acme",
        "Email domains": "acme.example",
        "QuickBooks customer": "",
        "Invoice code": "AC",
        "Notes": "",
        "Active": "yes",
    },
    "consultants": {
        "Consultant": "Priya Shah",
        "Initials": "",
        "Other names": "",
        "Email": "priya@shah.example",
        "Type": "contractor",
        "Vendor company": "",
        "Paid by": "bank transfer",
        "Pay timing (days)": "15",
        "Active": "yes",
    },
    "vendors": {
        "Vendor company": "BluePeak Staffing",
        "Contact emails": "",
        "Paid by": "bank transfer",
        "Pay timing (days)": "30",
        "Active": "yes",
    },
    "engagements": {
        "Consultant": "Priya Shah",
        "Client": "Acme Corp",
        "End client": "",
        "Role": "Developer",
        "Start date": "2026-01-01",
        "End date": "",
        "Billing schedule": "monthly",
        "First period start": "",
        "Bill rate": "100.00",
        "Pay rate": "70.00",
        "Rates from": "2026-01-01",
        "Send automatically": "no",
        "Active": "yes",
    },
}


def _workbook(rows: dict[str, list[dict[str, str]]]) -> RawWorkbook:
    def sheet(name: str) -> list[RawRow]:
        return [
            RawRow(number, {**_DEFAULT_ROWS[name], **cells})
            for number, cells in enumerate(rows.get(name, []), start=2)
        ]

    return RawWorkbook(
        clients=sheet("clients"),
        consultants=sheet("consultants"),
        vendors=sheet("vendors"),
        engagements=sheet("engagements"),
    )


def _message_id(key: str) -> str:
    return f"<{key}@eval>"


def _sha(email_key: str, filename: str) -> str:
    return hashlib.sha256(f"{email_key}/{filename}".encode()).hexdigest()


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
    for mail in situation.emails:
        store.record_message(
            StoredMessage(
                message_id=_message_id(mail.key),
                in_reply_to="",
                references=(),
                from_address=mail.sender,
                to_addresses="timesheets@icon-technologies.com",
                subject=mail.subject,
                body_text=mail.body,
                received_at=datetime.fromisoformat(mail.received),
                kind=MessageKind(mail.kind),
                processed=False,
                attachments=tuple(
                    StoredAttachment(
                        filename=name,
                        mime_type="application/pdf",
                        sha256=_sha(mail.key, name),
                        size_bytes=1000,
                    )
                    for name in mail.attachments
                ),
            ),
            {},
        )
        store.mark_processed(_message_id(mail.key))
    for spec in situation.items:
        snapshot = _snapshot(spec.client_code, spec.consultant_code, spec.consultant)
        snapshot = EngagementSnapshot(
            **{**snapshot.__dict__, "billing_emails": list(spec.billing_emails)}
        )
        item = store.create_item(
            spec.consultant,
            spec.client,
            spec.period(),
            ItemStatus.RECEIVED,
            snapshot,
        )
        # Through the same status changes a run would make, never round them.
        for step in _PATH_TO[spec.status]:
            item = store.change_status(item.id, step, {"why": "eval situation"})
        ids[spec.key] = item.id
        for timesheet in spec.timesheets:
            source = next(m for m in situation.emails if m.key == timesheet.email)
            store.record_timesheet(
                TimesheetRecord(
                    item_id=item.id,
                    sha256=_sha(source.key, source.attachments[0]),
                    reading=dict(timesheet.reading),
                    model="eval",
                    prompt_version="eval",
                    is_duplicate=timesheet.is_duplicate,
                    is_correction=timesheet.is_correction,
                )
            )
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
    workbook = (
        None
        if situation.engagement_list is None
        else parse_workbook(_workbook(situation.engagement_list))
    )
    looking = Looking(store=store, accounting=accounting, workbook=workbook)

    review = situation.review
    if review.item is None:
        return _build_without_an_item(situation, looking)
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
    return _built(situation, looking, record, reviews)


def _build_without_an_item(situation: Situation, looking: Looking) -> Built:
    """A question with no item: one email that could not be placed or read,
    or the engagement list's own problems -- written as the run writes them."""
    store = looking.store
    assert isinstance(store, FakeStore)
    review = situation.review
    asked = [review.message, *review.messages] if review.message else list(review.messages)
    for text in asked:
        store.open_review(None, review.code, text)
    extra: dict[str, object] = {}
    if review.email is not None:
        message_id = _message_id(review.email)
        mail = store.get_message(message_id)
        assert mail is not None, f"no email {review.email} in the situation"
        about = f"an email from {mail.from_address}"
        if review.set_aside:
            # As application/set_aside.py remembers it.
            store.set_state(
                "set_aside",
                json.dumps(
                    [
                        {
                            "message_id": message_id,
                            "code": review.code,
                            "review_message": review.message,
                            "sender": mail.from_address,
                            "subject": mail.subject,
                        }
                    ]
                ),
            )
            extra["set_aside"] = message_id
        else:
            extra["about_email"] = message_id
    else:
        about = "the engagement list"
        extra["list_problems"] = asked
    email = emails.needs_review(ADMIN, about, asked)
    body = email.body
    if situation.what_i_read:
        first, _, rest = body.partition("\n")
        body = "\n".join([first, "", "What I read:", *situation.what_i_read, rest])
    store.record_outgoing(
        "review_email", "review:eval", None, {**email.payload(), "body": body, **extra}
    )
    [record] = store.outgoing_records()
    reviews = [r for r in store.open_reviews() if f"- {r.message}" in body]
    return _built(situation, looking, record, reviews)


def _built(
    situation: Situation, looking: Looking, record: OutgoingRecord, reviews: list[ReviewRecord]
) -> Built:
    return Built(
        looking=looking,
        problem=problem_for(looking.store, record, reviews),
        forms=forms_for(record, reviews),
        seen=_everything_shown(looking, [_message_id(mail.key) for mail in situation.emails]),
    )


def _everything_shown(looking: Looking, message_ids: list[str]) -> str:
    """What every tool could show about this situation: a fact the
    investigator offers must be in here or in the problem, or it invented it."""
    toolbox = ReadOnlyToolbox(looking)
    shown: list[str] = []
    for spec in toolbox.specs():
        if not spec.input_schema["required"]:
            shown.append(toolbox.call(spec.name, {})[0])
    for item in looking.store.list_items():
        shown.append(toolbox.call("describe_item", {"item_id": item.id})[0])
        shown.append(toolbox.call("item_timesheets", {"item_id": item.id})[0])
    for message_id in message_ids:
        shown.append(toolbox.call("describe_email", {"message_id": message_id})[0])
    if looking.workbook is not None:
        names = {c.name for c in looking.workbook.consultants} | {
            c.name for c in looking.workbook.clients
        }
        for name in names:
            shown.append(toolbox.call("look_up_engagements", {"name_or_address": name})[0])
    return "\n".join(shown)


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
    waiting: list[str] = []
    for case in cases:
        built = build_situation(case.situation)
        case_dir = args.cases / case.name
        if investigator is None and not has_recorded(case_dir):
            waiting.append(case.name)
            continue
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
        else:
            recorded = read_recorded(case_dir)
        report.scores.append(
            score_investigation(case, recorded, built.problem, built.forms, built.seen)
        )
    print(report.format())
    if waiting:
        print(f"\nNot yet recorded ({len(waiting)}): {', '.join(waiting)}.")
        print("Run `fops eval-investigator --live` to record them.")

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
    if thresholds.re_record_for is not None:
        print(
            f"\nWAITING FOR A LIVE RUN: the recorded answers were written by"
            f" {thresholds.prompt_version}, and the investigator now uses"
            f" {thresholds.re_record_for}. The floor is not enforced until"
            " `fops eval-investigator --live` records its answers."
        )
        return 0
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
            "re_record_for": None,
        }
    )
    path.write_text(json.dumps(raw, indent=2) + "\n")


def scored_case(case: InvestigationCase, recorded: Recorded) -> list[str]:
    """The misses for one case, for tests."""
    built = build_situation(case.situation)
    return score_investigation(case, recorded, built.problem, built.forms, built.seen).misses
