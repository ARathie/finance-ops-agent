"""Entry point for the fops command."""

import argparse
import json
import tempfile
from datetime import date
from pathlib import Path

from finance_ops_agent import __version__
from finance_ops_agent.adapters.excel.engagement_list import CsvEngagementList
from finance_ops_agent.adapters.excel.tracking import tracking_sheet_bytes
from finance_ops_agent.adapters.fakes.clock import FakeClock
from finance_ops_agent.adapters.fakes.mailbox import FakeMailbox
from finance_ops_agent.adapters.fakes.reader import FakeReader
from finance_ops_agent.adapters.fakes.sender import FakeSender
from finance_ops_agent.adapters.pdf.writer import TextPdfRenderer
from finance_ops_agent.adapters.quickbooks.manual import ManualQuickBooks
from finance_ops_agent.adapters.sqlite.store import SqliteStore, open_database
from finance_ops_agent.application.run import Mode, RunDeps, Settings, run_once
from finance_ops_agent.domain.reading import TimesheetReading
from finance_ops_agent.ports.store import Store


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fops",
        description="Billing agent for Icon Technologies (developer/operator command line).",
    )
    parser.add_argument("--version", action="version", version=f"fops {__version__}")
    commands = parser.add_subparsers(dest="command")

    dry_run = commands.add_parser(
        "dry-run", help="run the whole flow without sending anything to a client"
    )
    dry_run.add_argument(
        "--fake",
        action="store_true",
        help="run on fixture emails with fake adapters and no network",
    )
    dry_run.add_argument(
        "--fixtures",
        type=Path,
        default=Path("tests/fixtures/fake_run"),
        help="folder with mailbox/, engagements/, readings/, and today.txt",
    )
    dry_run.add_argument(
        "--data",
        type=Path,
        default=None,
        help="data folder (default: a fresh temporary folder)",
    )
    dry_run.add_argument("--today", type=date.fromisoformat, default=None)
    dry_run.add_argument(
        "--mode",
        type=Mode,
        choices=list(Mode),
        default=Mode.DRY_RUN,
        help="dry_run (default), ask_first, or auto — all still on fakes",
    )

    status = commands.add_parser("status", help="print the items and open reviews")
    status.add_argument("--data", type=Path, required=True)

    evaluate = commands.add_parser(
        "eval", help="score the timesheet reader against the made-up test set"
    )
    evaluate.add_argument("--cases", type=Path, default=Path("tests/evals/timesheets"))
    evaluate.add_argument("--thresholds", type=Path, default=Path("tests/evals/thresholds.json"))
    evaluate.add_argument(
        "--live",
        action="store_true",
        help="call the real model (costs money; needs ANTHROPIC_API_KEY)"
        " and overwrite each case's recorded.json",
    )

    return parser


def _open_store(data: Path) -> SqliteStore:
    data.mkdir(parents=True, exist_ok=True)
    return SqliteStore(open_database(data / "agent.db"), files_dir=data / "files")


def _build_fake_deps(fixtures: Path, data: Path, today: date | None, mode: Mode) -> RunDeps:
    readings: dict[str, TimesheetReading] = {}
    readings_dir = fixtures / "readings"
    if readings_dir.is_dir():
        for path in sorted(readings_dir.glob("*.json")):
            attachment_name = path.name.removesuffix(".json")
            readings[attachment_name] = TimesheetReading.model_validate(
                json.loads(path.read_text())
            )
    if today is None:
        today_file = fixtures / "today.txt"
        today = (
            date.fromisoformat(today_file.read_text().strip())
            if today_file.exists()
            else date.today()  # noqa: DTZ011 - fake runs only; the real run uses the Clock port
        )
    store = _open_store(data)
    renderer = TextPdfRenderer()
    return RunDeps(
        engagement_list=CsvEngagementList(fixtures / "engagements"),
        inbox=FakeMailbox(fixtures / "mailbox"),
        reader=FakeReader(readings),
        store=store,
        clock=FakeClock(today),
        settings=Settings(mode=mode),
        sender=FakeSender(data / "outbox"),
        accounting=ManualQuickBooks(store, renderer, today),
        renderer=renderer,
        tracking_path=data / "tracking.xlsx",
        render_tracking=tracking_sheet_bytes,
    )


def _print_status(store: Store) -> None:
    items = store.list_items()
    print(f"{len(items)} timesheet item(s):")
    for item in items:
        amounts = ""
        if item.approved_hours is not None and item.invoice_amount is not None:
            amounts = (
                f" — {item.approved_hours} hours, invoice {item.invoice_amount},"
                f" owed {item.amount_owed}"
            )
        print(
            f"  {item.consultant} at {item.client},"
            f" {item.period.start} to {item.period.end}: {item.status}{amounts}"
        )
    reviews = store.open_reviews()
    print(f"{len(reviews)} open review(s):")
    for review in reviews:
        print(f"  [{review.code}] {review.message}")


def _command_dry_run(args: argparse.Namespace) -> int:
    if not args.fake:
        print("Only `fops dry-run --fake` exists so far; the real mailbox arrives with PR 8.")
        return 2
    data: Path = args.data if args.data is not None else Path(tempfile.mkdtemp(prefix="fops-"))
    deps = _build_fake_deps(args.fixtures, data, args.today, args.mode)
    report = run_once(deps)
    print(f"{args.mode} run on fixtures in {args.fixtures} (data in {data}):")
    for line in report.lines:
        print(f"  {line}")
    print()
    _print_status(deps.store)
    outgoing = deps.store.outgoing_records()
    print(f"{len(outgoing)} email(s), each written down once and sent to {data / 'outbox'}:")
    for record in outgoing:
        subject = record.payload.get("subject", "")
        print(f"  [{record.status}] {record.kind}: {subject}")
    return 0


def _command_status(args: argparse.Namespace) -> int:
    _print_status(_open_store(args.data))
    return 0


def _command_eval(args: argparse.Namespace) -> int:
    from finance_ops_agent.application.eval_runner import (
        EvalCase,
        Thresholds,
        below_thresholds,
        load_cases,
        run_eval,
    )

    cases = load_cases(args.cases)
    if args.live:
        import os

        from finance_ops_agent.adapters.claude.reader import ClaudeReader

        if not os.environ.get("ANTHROPIC_API_KEY"):
            print("The live run needs ANTHROPIC_API_KEY. CI replays recorded.json only.")
            return 2
        reader = ClaudeReader(model=os.environ.get("FOPS_MODEL", "claude-opus-5"))

        def read(case: EvalCase) -> TimesheetReading:
            reading = reader.read_timesheet(case.input_path.read_bytes(), case.input_path.name, "")
            recorded = case.input_path.parent / "recorded.json"
            recorded.write_text(reading.model_dump_json(indent=2))
            return reading

    else:

        def read(case: EvalCase) -> TimesheetReading:
            recorded = case.input_path.parent / "recorded.json"
            if not recorded.exists():
                raise SystemExit(f"{case.name} has no recorded.json; run `fops eval --live` once")
            return TimesheetReading.model_validate(json.loads(recorded.read_text()))

    report = run_eval(cases, read)
    print(report.format())
    thresholds = Thresholds.model_validate(json.loads(args.thresholds.read_text()))
    problems = below_thresholds(report, thresholds)
    for problem in problems:
        print(f"BELOW THRESHOLD: {problem}")
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "dry-run":
        return _command_dry_run(args)
    if args.command == "status":
        return _command_status(args)
    if args.command == "eval":
        return _command_eval(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
