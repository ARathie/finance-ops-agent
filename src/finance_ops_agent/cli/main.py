"""Entry point for the fops command."""

import argparse
import json
import tempfile
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

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
from finance_ops_agent.application.context import effective_mode
from finance_ops_agent.application.run import Mode, RunDeps, Settings, run_once
from finance_ops_agent.domain.reading import TimesheetReading
from finance_ops_agent.ports.accounting import AccountingSystem
from finance_ops_agent.ports.store import Store

if TYPE_CHECKING:
    from finance_ops_agent.adapters.claude.reader import ClaudeReader as ClaudeReaderType
    from finance_ops_agent.adapters.email.client import MailAccount
    from finance_ops_agent.adapters.email.sender import SmtpSender
    from finance_ops_agent.application.eval_runner import UsageReport
    from finance_ops_agent.cli.doctor import Check
    from finance_ops_agent.config import Config, MailSettings
    from finance_ops_agent.ports.reader import TokenUsage


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

    doctor = commands.add_parser(
        "doctor",
        help="check every credential and setting; sends nothing to a client",
    )
    doctor.add_argument(
        "--send-test-email",
        action="store_true",
        help="also send one test email to FOPS_ADMIN_EMAIL",
    )

    run_cmd = commands.add_parser("run", help="one real run against the mailbox in the environment")
    run_cmd.add_argument(
        "--mode",
        type=Mode,
        choices=list(Mode),
        default=None,
        help="lower FOPS_MODE for this run; while FOPS_MODE=dry_run nothing can raise it",
    )

    connect = commands.add_parser(
        "qbo-connect", help="the one-time QuickBooks sign-in (run with Kevin present)"
    )
    connect.add_argument(
        "--port",
        type=int,
        default=None,
        help="loopback port for the redirect (must match the Intuit app)",
    )

    backup_cmd = commands.add_parser("backup", help="zip the data folder")
    backup_cmd.add_argument(
        "--to",
        type=Path,
        default=None,
        help="where to put the zip (default: <data>/backups)",
    )

    restore_cmd = commands.add_parser("restore", help="unpack a backup into an empty data folder")
    restore_cmd.add_argument("archive", type=Path)
    restore_cmd.add_argument(
        "--force",
        action="store_true",
        help="overwrite a data folder that is not empty",
    )

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
    evaluate.add_argument(
        "--price-input",
        type=int,
        default=None,
        help="cents per million input tokens, if the built-in price is stale",
    )
    evaluate.add_argument(
        "--price-output",
        type=int,
        default=None,
        help="cents per million output tokens, if the built-in price is stale",
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
        # A real dry run is `fops run` with FOPS_MODE=dry_run (or --mode dry_run),
        # which is the safest real mode: it sends nothing to a client.
        args.mode = Mode.DRY_RUN
        return _command_run(args)
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


def _accounting(
    config: "Config", store: SqliteStore, renderer: TextPdfRenderer, today: date
) -> AccountingSystem:
    """Manual mode until QuickBooks Online is connected (docs/decisions.md #11)."""
    if config.accounting != "quickbooks":
        return ManualQuickBooks(store, renderer, today)
    from finance_ops_agent.adapters.quickbooks.client import QuickBooksClient
    from finance_ops_agent.adapters.quickbooks.online import QuickBooksOnline
    from finance_ops_agent.adapters.quickbooks.tokens import TokenStore
    from finance_ops_agent.config import QuickBooksSettings

    settings = QuickBooksSettings.from_env()
    client = QuickBooksClient(
        TokenStore(config.qbo_token_path), settings.client_id, settings.client_secret
    )
    return QuickBooksOnline(client, settings.item_name, today)


def _mail_account(mail: "MailSettings") -> "MailAccount":
    from finance_ops_agent.adapters.email.client import MailAccount

    return MailAccount(
        imap_host=mail.imap_host,
        imap_port=mail.imap_port,
        imap_security=mail.imap_security,
        smtp_host=mail.smtp_host,
        smtp_port=mail.smtp_port,
        smtp_security=mail.smtp_security,
        username=mail.username,
        password=mail.password,
        folder_prefix=mail.folder_prefix,
        sent_folder=mail.sent_folder,
    )


def _real_deps(mode_override: "Mode | None" = None) -> RunDeps:
    """Wire the real adapters from the environment (docs/technical-design.md)."""
    from finance_ops_agent.adapters.claude.reader import ClaudeReader
    from finance_ops_agent.adapters.clock import SystemClock
    from finance_ops_agent.adapters.email.inbox import ImapInbox
    from finance_ops_agent.adapters.email.sender import SmtpSender
    from finance_ops_agent.adapters.excel.engagement_list import ExcelEngagementList
    from finance_ops_agent.config import Config, MailSettings

    config = Config.from_env()
    mail = MailSettings.from_env()
    account = _mail_account(mail)
    store = _open_store(config.data_dir)
    renderer = TextPdfRenderer()
    clock = SystemClock(config.timezone)
    # The first run reads from today unless MAIL_START_DATE says otherwise, so a
    # mailbox with history in it is not processed from the beginning of time.
    start_date = mail.start_date or _remembered_start_date(store, clock.today())
    return RunDeps(
        engagement_list=ExcelEngagementList(config.engagement_list),
        inbox=ImapInbox(account, config.agent_mailbox, start_date),
        reader=ClaudeReader(model=config.model),
        store=store,
        clock=clock,
        settings=Settings(
            admin_email=config.admin_email,
            mode=effective_mode(config.mode, mode_override),
            agent_mailbox=config.agent_mailbox,
        ),
        sender=SmtpSender(account, config.agent_mailbox, clock.now),
        accounting=_accounting(config, store, renderer, clock.today()),
        renderer=renderer,
        tracking_path=config.data_dir / "tracking.xlsx",
        render_tracking=tracking_sheet_bytes,
    )


START_DATE_KEY = "mail_start_date"


def _remembered_start_date(store: SqliteStore, today: date) -> date:
    """The day of the first real run, written down once and kept."""
    remembered = store.get_state(START_DATE_KEY)
    if remembered:
        return date.fromisoformat(remembered)
    store.set_state(START_DATE_KEY, today.isoformat())
    return today


def _command_run(args: argparse.Namespace) -> int:
    from finance_ops_agent import logs
    from finance_ops_agent.adapters.lockfile import AlreadyRunning, RunLock
    from finance_ops_agent.config import Config, MissingSettingError

    try:
        config = Config.from_env()
        deps = _real_deps(args.mode)
    except MissingSettingError as error:
        print(f"Not configured: {error}. Run `fops doctor` for the whole list.")
        return 2
    if args.mode is not None and deps.settings.mode is not args.mode:
        print(
            f"FOPS_MODE={config.mode} is set, so this run is {deps.settings.mode},"
            f" not {args.mode}. Change the setting to run any other way."
        )
    logs.configure(config.data_dir / "fops.log")
    try:
        # One run at a time: the scheduler fires every 15 minutes and a slow run
        # must never overlap with the next one.
        with RunLock(config.data_dir / "run.lock"):
            report = run_once(deps)
    except AlreadyRunning as error:
        print(error)
        return 0  # not a failure: the next run will pick things up
    _warn_about_ageing_connections(config)
    print(f"Run finished in {deps.settings.mode} mode:")
    for line in report.lines:
        print(f"  {line}")
    print(
        f"  {report.messages_stored} new message(s),"
        f" {report.emails_sent} email(s) sent,"
        f" {report.reviews_opened} review(s) opened"
    )
    return 0


def _command_doctor(args: argparse.Namespace) -> int:
    from finance_ops_agent.adapters.excel.engagement_list import ExcelEngagementList
    from finance_ops_agent.cli import doctor as checks
    from finance_ops_agent.cli.doctor import Check, CheckResult
    from finance_ops_agent.config import ENV_FILE, Config, MailSettings, MissingSettingError
    from finance_ops_agent.domain.engagements import parse_workbook
    from finance_ops_agent.ports.inbox import IGNORED_FOLDER, NEEDS_REVIEW_FOLDER, PROCESSED_FOLDER

    results: list[Check] = []
    env_path = Path(ENV_FILE)
    if env_path.is_file():
        results.append(Check("settings file", CheckResult.PASS, f"read {env_path.resolve()}"))
    else:
        results.append(
            Check(
                "settings file",
                CheckResult.SKIP,
                f"no {ENV_FILE} in {Path.cwd()};"
                " settings have to come from the environment instead",
            )
        )
    try:
        config = Config.from_env()
    except MissingSettingError as error:
        results.append(Check("settings", CheckResult.FAIL, str(error)))
        for check in results:
            print(check.line())
        return 1
    results.append(
        Check(
            "settings",
            CheckResult.PASS,
            f"mode {config.mode}, timezone {config.timezone}, accounting {config.accounting}",
        )
    )

    def load_list() -> tuple[int, list[str]]:
        parsed = parse_workbook(ExcelEngagementList(config.engagement_list).load())
        return len(parsed.engagements), [
            f"{problem.sheet} row {problem.row_number}: {problem.message}"
            for problem in parsed.problems
        ]

    results.append(checks.check_engagement_list(load_list))

    def describe_database() -> str:
        store = _open_store(config.data_dir)
        return f"{len(store.list_items())} item(s) in {config.data_dir / 'agent.db'}"

    results.append(checks.check_database(describe_database))
    results.append(checks.check_claude_api(config.model))

    try:
        mail = MailSettings.from_env()
    except MissingSettingError as error:
        results.append(Check("mailbox", CheckResult.FAIL, str(error)))
    else:
        start = mail.start_date.isoformat() if mail.start_date else "the first run's date"
        results.append(Check("mail start date", CheckResult.PASS, f"reading mail from {start}"))
        account = _mail_account(mail)
        results.append(checks.check_mailbox_is_the_agents(account, config.agent_mailbox))
        results.append(checks.check_imap_login(account))
        results.append(
            checks.check_agent_folders(
                account, (PROCESSED_FOLDER, NEEDS_REVIEW_FOLDER, IGNORED_FOLDER)
            )
        )
        results.append(checks.check_sent_folder(account))
        results.append(checks.check_smtp_login(account))
        if args.send_test_email:
            from finance_ops_agent.adapters.clock import SystemClock
            from finance_ops_agent.adapters.email.sender import SmtpSender

            sender = SmtpSender(account, config.agent_mailbox, SystemClock(config.timezone).now)
            results.append(_send_test_email(sender, config.admin_email))

    if config.accounting == "quickbooks":
        results.extend(_quickbooks_checks(config))
    else:
        results.append(
            Check(
                "accounting",
                CheckResult.SKIP,
                "manual mode: I number and render the invoice, you enter it into"
                " QuickBooks Desktop",
            )
        )

    for check in results:
        print(check.line())
    failures = [check for check in results if check.result is CheckResult.FAIL]
    if failures:
        print(f"\n{len(failures)} check(s) failed. Nothing was sent to a client.")
        return 1
    print("\nEverything checks out. Nothing was sent to a client.")
    return 0


def _quickbooks_checks(config: "Config") -> list["Check"]:
    from finance_ops_agent.adapters.excel.engagement_list import ExcelEngagementList
    from finance_ops_agent.adapters.quickbooks.client import QuickBooksClient
    from finance_ops_agent.adapters.quickbooks.online import QuickBooksOnline
    from finance_ops_agent.adapters.quickbooks.tokens import TokenStore
    from finance_ops_agent.cli.doctor import (
        Check,
        CheckResult,
        check_quickbooks_customers,
        check_quickbooks_tokens,
    )
    from finance_ops_agent.config import MissingSettingError, QuickBooksSettings
    from finance_ops_agent.domain.engagements import parse_workbook

    try:
        settings = QuickBooksSettings.from_env()
    except MissingSettingError as error:
        return [Check("quickbooks", CheckResult.FAIL, str(error))]
    store = TokenStore(config.qbo_token_path)
    results = [check_quickbooks_tokens(store)]
    if results[0].result is CheckResult.FAIL:
        return results
    client = QuickBooksClient(store, settings.client_id, settings.client_secret)
    accounting = QuickBooksOnline(client, settings.item_name, date.today())  # noqa: DTZ011

    def wanted_customers() -> list[str]:
        parsed = parse_workbook(ExcelEngagementList(config.engagement_list).load())
        return sorted(
            {
                client_row.quickbooks_customer or client_row.legal_name
                for client_row in parsed.clients
                if client_row.active
            }
        )

    results.append(check_quickbooks_customers(accounting, wanted_customers))
    return results


def _send_test_email(sender: "SmtpSender", admin_email: str) -> "Check":
    """Send one email to Kevin and prove the copy landed in Sent."""
    from email.utils import make_msgid

    from finance_ops_agent.cli.doctor import Check, CheckResult
    from finance_ops_agent.domain.emails import OutgoingEmail

    email = OutgoingEmail(
        to=(admin_email,),
        subject="fops doctor: this mailbox works",
        body=(
            "This is the test email from `fops doctor`. Nothing was sent to"
            " any client. If you got this, the agent can read and send mail."
        ),
    )
    message_id = make_msgid(domain=admin_email.rsplit("@", 1)[-1])
    try:
        sender.send(email, {}, message_id)
        sender.save_sent_copy(email, {}, message_id)
        if not sender.find_sent(message_id):
            return Check(
                "test email",
                CheckResult.FAIL,
                f"sent one email to {admin_email}, but could not find the copy in Sent",
            )
        return Check(
            "test email", CheckResult.PASS, f"sent one email to {admin_email}; copy is in Sent"
        )
    except Exception as error:
        return Check("test email", CheckResult.FAIL, str(error)[:300])


def _warn_about_ageing_connections(config: "Config") -> None:
    """A QuickBooks refresh token that is about to age out needs a person."""
    if config.accounting != "quickbooks":
        return
    from finance_ops_agent.adapters.quickbooks.tokens import (
        NotConnected,
        TokenStore,
        utcnow,
    )

    try:
        tokens = TokenStore(config.qbo_token_path).load()
    except NotConnected:
        return
    warning = tokens.refresh_token_warning(utcnow())
    if warning:
        print(f"Warning: {warning}")


def _command_backup(args: argparse.Namespace) -> int:
    from finance_ops_agent.application.backup import back_up
    from finance_ops_agent.config import Config, MissingSettingError

    try:
        config = Config.from_env()
    except MissingSettingError as error:
        print(f"Not configured: {error}")
        return 2
    destination = args.to or (config.data_dir / "backups")
    result = back_up(
        config.data_dir,
        destination,
        date.today(),  # noqa: DTZ011 - a backup's filename, not a billing date
        store=_open_store(config.data_dir),
    )
    print(f"Backed up {result.files} file(s) to {result.path}")
    print("Copy it somewhere off this machine (OneDrive/SharePoint), and try")
    print(f"`fops restore {result.path}` into an empty folder now and then.")
    return 0


def _command_restore(args: argparse.Namespace) -> int:
    from finance_ops_agent.application.backup import RestoreRefused, restore
    from finance_ops_agent.config import Config, MissingSettingError

    try:
        config = Config.from_env()
    except MissingSettingError as error:
        print(f"Not configured: {error}")
        return 2
    try:
        count = restore(args.archive, config.data_dir, force=args.force)
    except RestoreRefused as error:
        print(error)
        return 1
    print(f"Restored {count} file(s) into {config.data_dir}")
    return 0


def _command_qbo_connect(args: argparse.Namespace) -> int:
    from finance_ops_agent.adapters.quickbooks.client import QuickBooksReconnect
    from finance_ops_agent.adapters.quickbooks.connect import DEFAULT_PORT, connect
    from finance_ops_agent.adapters.quickbooks.tokens import TokenStore
    from finance_ops_agent.config import Config, MissingSettingError, QuickBooksSettings

    try:
        config = Config.from_env()
        settings = QuickBooksSettings.from_env()
    except MissingSettingError as error:
        print(f"Not configured: {error}")
        return 2
    store = TokenStore(config.qbo_token_path)
    try:
        tokens = connect(
            store,
            settings.client_id,
            settings.client_secret,
            settings.environment,
            port=args.port or DEFAULT_PORT,
        )
    except QuickBooksReconnect as error:
        print(f"Could not connect: {error}")
        return 1
    print(
        f"Connected to the {tokens.environment} company {tokens.realm_id}."
        f" Tokens are in {store.path} (only you can read them)."
    )
    if settings.environment == "sandbox":
        print("This is the sandbox. Set QBO_ENVIRONMENT=production when you are ready.")
    return 0


def _command_status(args: argparse.Namespace) -> int:
    _print_status(_open_store(args.data))
    return 0


def _stamp_live_thresholds(path: Path, model: str, prompt_version: str) -> None:
    """Record which model and prompt wrote the answers now in `recorded.json`.

    The threshold numbers themselves are not touched: a person reads the live
    scores and decides what the floor should be (docs/roadmap.md PR 12).
    """
    from finance_ops_agent.application.eval_runner import Thresholds, stamp_live_provenance

    raw = json.loads(path.read_text())
    stamped = stamp_live_provenance(
        Thresholds.model_validate(raw),
        model=model,
        prompt_version=prompt_version,
        recorded_on=date.today(),
    )
    raw.update(json.loads(stamped.model_dump_json(include=set(_LIVE_PROVENANCE_FIELDS))))
    path.write_text(json.dumps(raw, indent=2) + "\n")


_LIVE_PROVENANCE_FIELDS = ("source", "model", "prompt_version", "recorded_on")


def _usage_report(
    usage: "TokenUsage", cases: int, model: str, args: argparse.Namespace
) -> "UsageReport":
    """What the live run spent. Overridden prices beat the built-in table."""
    from finance_ops_agent.application.eval_runner import MODEL_PRICES, ModelPrices, UsageReport

    prices = MODEL_PRICES.get(model)
    if args.price_input is not None and args.price_output is not None:
        # Cache rates follow input: 1.25x to write, 0.1x to read.
        prices = ModelPrices(
            input_cents_per_million=args.price_input,
            output_cents_per_million=args.price_output,
            cache_write_cents_per_million=args.price_input * 125 // 100,
            cache_read_cents_per_million=args.price_input // 10,
        )
    return UsageReport(usage=usage, cases=cases, prices=prices, model=model)


def _command_eval(args: argparse.Namespace) -> int:
    from finance_ops_agent.application.eval_runner import (
        BOOTSTRAP_WARNING,
        EvalCase,
        Thresholds,
        below_thresholds,
        load_cases,
        run_eval,
    )

    cases = load_cases(args.cases)
    live_model: str | None = None
    live_prompt_version: str | None = None
    live_reader: ClaudeReaderType | None = None
    if args.live:
        import os

        from finance_ops_agent.adapters.claude.reader import ClaudeReader

        try:
            reader = ClaudeReader(model=os.environ.get("FOPS_MODEL", "claude-opus-5"))
        except Exception as error:
            print(f"The live run needs Claude credentials: {error}")
            print("Set ANTHROPIC_API_KEY. CI replays recorded.json only.")
            return 2
        live_model, live_prompt_version = reader.model_name, reader.prompt_version
        live_reader = reader

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

    if live_reader is not None and live_model is not None:
        print()
        print(_usage_report(live_reader.usage, len(cases), live_model, args).format())

    if live_model is not None and live_prompt_version is not None:
        _stamp_live_thresholds(args.thresholds, live_model, live_prompt_version)
        print(f"\nRecorded answers are now the model's own ({live_model}, {live_prompt_version}).")
        print("Read the scores above and decide whether they are good enough (roadmap PR 12).")

    thresholds = Thresholds.model_validate(json.loads(args.thresholds.read_text()))
    if thresholds.proves_the_harness_only():
        print(f"\nWARNING: {BOOTSTRAP_WARNING}")
    problems = below_thresholds(report, thresholds)
    for problem in problems:
        print(f"BELOW THRESHOLD: {problem}")
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    from finance_ops_agent.config import ENV_FILE, load_env_file

    parser = build_parser()
    args = parser.parse_args(argv)
    # Run by hand there is nothing to put the settings in the environment, so
    # read .env from the folder we are in first (docs/running-it.md).
    load_env_file(Path(ENV_FILE))
    if args.command == "dry-run":
        return _command_dry_run(args)
    if args.command == "status":
        return _command_status(args)
    if args.command == "eval":
        return _command_eval(args)
    if args.command == "doctor":
        return _command_doctor(args)
    if args.command == "run":
        return _command_run(args)
    if args.command == "qbo-connect":
        return _command_qbo_connect(args)
    if args.command == "backup":
        return _command_backup(args)
    if args.command == "restore":
        return _command_restore(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
