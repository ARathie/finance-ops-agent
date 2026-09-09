# Technical design

For the people (and coding agents) building the agent. The business behaviour is fixed by `how-it-works.md`, `engagement-list.md`, `timesheet-checks.md`, `status-tracking.md`, and `emails.md`; this file says how to build it. Language: Python 3.11+.

## Shape of the system

One small Python program that does a run every 15 minutes. In production it runs as a container on a small always-on server (`fops serve`); for the first real-life test it runs from a Mac (`fops run` on a launchd timer). It never depends on anyone's personal computer being on (decision 22). Each run:

1. loads the engagement list workbook and validates it;
2. reads new mail from the agent mailbox since the last run;
3. for each new email: decides what it is (timesheet, Kevin's reply, client reply, other), stores it and its attachments, and processes it through the checks;
4. sends whatever emails are due (details for records, review requests, approval requests, billing emails, payment instructions);
5. once a day, asks QuickBooks Online which of our invoices are paid; on Mondays, sends the summary;
6. rewrites `tracking.xlsx`.

Nothing happens between runs, and the agent listens on no port: it only makes outgoing connections (to the mailbox, Anthropic, QuickBooks, the heartbeat service, and the backup bucket). There is no web page. Kevin interacts only by email; the developer/operator uses a small command line (`fops`).

### Layers

```
src/finance_ops_agent/
  domain/        pure rules: money and hours, billing periods, the checks, statuses, invoice and pay math. No I/O, no clock.
  application/   the steps of a run, in order, calling ports. Knows nothing about Rackspace, QuickBooks, or Claude.
  ports/         the interfaces the application needs (below). Plain Python Protocols.
  adapters/      one folder per real thing: email/ (IMAP and SMTP), quickbooks/, claude/, excel/ (engagement list + tracking sheet), sqlite/, pdf/, and fakes/ for every port.
  cli/           `fops` commands: run, serve, dry-run, status, doctor, qbo-connect, eval, backup, restore. Also the doctor's checks, which are all about a concrete adapter.
  config.py      settings from environment variables / .env
  logs.py        JSON lines logging; only the command line configures where they go
```

Rule: `domain/`, `application/`, and `ports/` never import from `adapters/` or `cli/`, and `tests/unit/test_layering.py` checks it file by file. Tests for the business rules run with fakes and no network.

### Ports (interfaces)

| Port | What the application asks of it | Fake | Real |
|---|---|---|---|
| `EngagementList` | `load() -> Workbook` (clients, consultants, vendors, engagements, with row numbers for error messages) | in-memory rows | reads `engagements.xlsx` / CSVs with `openpyxl` |
| `EmailInbox` | `new_messages(position) -> (messages, position)`, `download_attachment(...)`, `move(message, folder)` | folder of `.eml` files | IMAP at Rackspace Email (`integrations/email-imap-smtp.md`) |
| `EmailSender` | `send(email, attachments, message_id)` (returns when the server has taken it; raises `NotSent` / `RecipientRefused` when it has not), `save_sent_copy(email, attachments, message_id)`, `find_sent(message_id) -> found / not found` | writes `.eml` files to an outbox folder | SMTP, plus the IMAP Sent folder, at Rackspace Email |
| `TimesheetReader` | `classify(email) -> kind`, `read_timesheet(attachment, hints) -> TimesheetReading`, `read_reply(email, context) -> Answer` | scripted by attachment hash / canned answers | Claude |
| `AccountingSystem` | `create_invoice(invoice, item_id) -> (number, external_id, pdf)`, `find_invoice(item_id)`, `cancel_invoice(external_id)`, `paid_status(external_ids)` | in-memory | `ManualQuickBooks` (numbers invoices locally and renders the PDF; Kevin enters them) and `QuickBooksOnline` |
| `PdfRenderer` | `invoice_pdf(invoice) -> bytes` | same as real | HTML template + WeasyPrint (or ReportLab) |
| `Clock` | `today()`, `now()` in Icon's timezone | frozen | system clock |

Every real adapter and its fake pass the same contract tests.

## Data

### Storage

One SQLite file (`data/agent.db`) plus a folder of files (`data/files/<sha256>`) for attachments and generated PDFs. SQLAlchemy 2 for tables, Alembic for schema changes (SQLite needs `render_as_batch=True`). Open the database with WAL mode, a busy timeout, and foreign keys on, so `fops status` can read while a run is writing.

Tables (key columns only; the domain objects mirror them):

| Table | Purpose | Key columns |
|---|---|---|
| `messages` | every email seen | Message-ID (unique), raw sha256, In-Reply-To, References, from, to, subject, received_at, kind, processed_at |
| `attachments` | every attachment stored | message id, filename, mime type, sha256, size |
| `items` | one per consultant + billing period | consultant, client, period_start, period_end (unique together), status, engagement row snapshot (bill/pay rate cents, terms, contacts, row number), approved hours (hundredths), invoice amount (cents), amount owed (cents) |
| `timesheets` | each timesheet file processed | item id, attachment sha256, reading (JSON), model, prompt version, is_duplicate, is_correction |
| `review_items` | each review reason raised | item id or message id, code, message, status (open/answered/ignored), answer (JSON), answered_at |
| `invoices` | one per item (plus replacements) | item id, number, external id, amount cents, issue date, due date, pdf sha256, status (created/sent/paid/cancelled), replaces |
| `payment_instructions` | one per item (plus replacements) | item id, payee, amount cents, due date, method, emailed_at |
| `outgoing` | every email or accounting write, written down before it happens | kind, idempotency key (unique), item id, payload (JSON), status (pending/in_flight/done/failed), message id, accepted_at, external id, attempts, last error |
| `audit_log` | append-only history | when, what (status change, email sent, answer received, run started/finished), item id, details (JSON) |
| `state` | mailbox position (UIDVALIDITY and last UID), last daily QuickBooks check, last summary sent, invoice counter | key, value |

### Money and hours

- Money is stored and calculated as whole cents (`int`). Hours as whole hundredths of an hour (`int`). Never `float`, and not `Decimal` in the database either (SQLite would turn it into a float).
- `invoice_cents = round_half_up(hours_hundredths * bill_rate_cents / 100)`, same for pay. Computed once when the item becomes `ready`, stored on the item, and printed from there ever after. The total QuickBooks returns must equal it exactly.
- Dates are dates (no times) for periods and due dates. Timestamps are UTC. "Today" comes from the `Clock` port in Icon's timezone (`FOPS_TIMEZONE`, required).

### The timesheet reading

`TimesheetReading` (Pydantic model, also the form Claude fills in): consultant_name, client_name, end_client_name, period_start, period_end, daily_entries[{date, hours}], stated_total_hours, approval{kind: approved_status | approver_name_date | signature | forwarded_email | none, approver, date}, unusual_items[], and for each field `quote` (the words on the page) and `confidence` (high / medium / low). The application sums daily entries itself and compares with the stated total.

## Never twice

- **Reading:** a message whose Message-ID (or raw sha256) is already in `messages` is skipped. The mailbox position (last UID) is advanced only after the run's messages are stored, and a `UIDVALIDITY` change just means a full rescan, which the Message-ID check makes harmless.
- **Same file:** an attachment whose sha256 is already in `timesheets` is a duplicate: filed, noted, not processed.
- **One item per consultant + period:** enforced by a unique index. A new timesheet for an existing item is a duplicate (same content) or a correction (different content), never a new item.
- **Sending and creating:** before any email is sent or any invoice is created in QuickBooks, an `outgoing` row is written with an idempotency key (for example `billing-email:<item id>:<invoice number>`) in the same transaction as the status change. For an email, the row carries a Message-ID the agent made itself. The worker then: marks it `in_flight`; sends (SMTP) or creates (QuickBooks); records `accepted_at` / the external id immediately; saves a copy to the Sent folder; marks it `done`. On start-up, any `in_flight` row is reconciled first: look for the Message-ID in the Sent folder, or for the item id kept in the QuickBooks private note, before doing anything again. If an email cannot be accounted for either way after 10 minutes, the agent asks Kevin (`SEND_UNCERTAIN`) rather than guessing; `integrations/email-imap-smtp.md` has the exact rules.
- **Retries:** transient failures (network, 429, 5xx) retry with backoff across runs, up to 3 attempts, then become `SEND_FAILED` / `QUICKBOOKS_FAILED` review items. Permanent failures (4xx other than 429) become review items at once.
- **Overlapping runs:** a POSIX advisory lock (`flock`) on `data/run.lock` prevents two runs at the same time; the second run says who holds it and stops. The lock belongs to the process, so it is released even if a run is killed — a pid file could not promise that.

## Kevin's replies

Replies from Kevin's address that are in the thread of a review or approval email are matched to the item by the `In-Reply-To` and `References` headers, which name the agent's own Message-ID, and by an item reference in the original subject as a fallback. Approval replies are checked by code: the first word must be `approve` or `cancel`. Review replies are read by Claude into a structured answer (which field, what value) and applied by code; if the reading is not confident, the agent replies asking again. Replies from anyone else are ignored and reported in the summary.

## Modes and guardrails

`FOPS_MODE` is one of `dry_run`, `ask_first`, `auto`. A billing email goes out without asking only when all of these hold: mode is `auto`; the engagement row says "Send automatically = yes"; the item has no open review item; every confidence on consultant, period, hours, and approval is `high`; and the amount is within 25% of the engagement's last three invoices (if there are any). Otherwise the item is handled as `ask_first`: the invoice still happens, Kevin just sees it first, and the run says which condition did not hold. In `dry_run` nothing is sent to clients and nothing is created in QuickBooks.

`dry_run` is the kill switch, so it is checked before the guardrails and it wins over everything else: while `FOPS_MODE=dry_run` is set, `fops run --mode auto` still runs as a dry run and says so. The flag may only lower a live mode to `dry_run`, never raise one. Changing what the agent may send is a change to the setting, made deliberately, not a flag on one command.

## Configuration

Environment variables (from `.env` locally; `.env.example` lists them all): `FOPS_MODE`, `FOPS_TIMEZONE`, `FOPS_DATA_DIR`, `FOPS_ENGAGEMENT_LIST` (path), `FOPS_ADMIN_EMAIL` (Kevin), `FOPS_AGENT_MAILBOX`, `FOPS_MODEL` (default `claude-opus-5`), `ANTHROPIC_API_KEY`; the mailbox (`MAIL_IMAP_HOST`, `MAIL_IMAP_PORT`, `MAIL_IMAP_SECURITY`, `MAIL_SMTP_HOST`, `MAIL_SMTP_PORT`, `MAIL_SMTP_SECURITY`, `MAIL_USERNAME`, `MAIL_PASSWORD`, `MAIL_START_DATE`, `MAIL_FOLDER_PREFIX`, `MAIL_SENT_FOLDER`); QuickBooks (`QBO_CLIENT_ID`, `QBO_CLIENT_SECRET`, `QBO_ENVIRONMENT=sandbox|production`, `QBO_ITEM_NAME`) and `FOPS_ACCOUNTING=manual|quickbooks`; running as a service (`FOPS_RUN_EVERY_MINUTES`, `FOPS_BACKUP_AT`, `FOPS_HEARTBEAT_URL`, `FOPS_BACKUP_TARGET`). Rotating values (QuickBooks tokens and realm id, the mailbox position) live in files under `data/` with restricted permissions, written atomically.

## Running it

- Developer: `uv run fops dry-run --fake` runs the whole flow on fixture emails with fake adapters and no network. `uv run fops run` does a real run. `uv run fops status` prints the items and open reviews. `uv run fops doctor` checks every credential and setting without sending anything to a client. `uv run fops qbo-connect` does the one-time QuickBooks sign-in. `uv run fops eval` runs the timesheet reading test set. `uv run fops backup` zips `data/` and `uv run fops restore <zip>` unpacks it into an empty data folder. `uv run fops serve` (PR 14) is the long-running form: a run every 15 minutes, a backup nightly, a heartbeat after each, a clean stop on `SIGTERM`.
- Stage 1, the first real-life test (temporary): a Mac with a launchd job running `fops run` every 15 minutes; it only works while the Mac is awake and logged in, which is why it is temporary.
- Stage 2, production: a container image built by CI, running `fops serve` on a small always-on Linux server (or any container host with a persistent disk), `data/` on a mounted volume, nightly backups uploaded to object storage, and a heartbeat ping after every run to a check service that emails Ash and Kevin if the pings stop. The agent listens on no port. Logs are JSON lines in `data/fops.log` with item ids, codes, statuses, and counts, never attachment contents, email bodies, rates, or amounts.
- **`running-it.md` is the operator's page**: both stages step by step, the launchd, Compose, and systemd files, reading the log, backups and a real restore, the heartbeat, the QuickBooks expiry warning, and what to check when nothing is happening.
- Backups checkpoint the database before copying it (`PRAGMA wal_checkpoint(TRUNCATE)`, exposed as `Store.checkpoint`) and leave the `-wal`/`-shm` sidecars and `run.lock` out of the zip. Without the checkpoint the `.db` file alone can be missing committed work, which is a quietly incomplete backup; a test proves it.

## Security and privacy

- The mailbox credentials belong to the agent's own mailbox at Rackspace and can reach nothing else; the QuickBooks app has only the accounting scope; secrets are never in git and never in the container image. The agent makes outgoing connections only and needs no inbound firewall rule.
- Email content and attachments are untrusted input: the model is asked to read them, never to follow instructions in them; nothing in an email can change a rate, a recipient, or a mode.
- Only Kevin's address can answer reviews or approve invoices, and only in-thread.
- The pay rate never appears in anything sent to a client; the bill rate never appears in anything sent to a consultant or vendor.
- Timesheets contain names and hours; they are sent to Anthropic's API for reading. Nothing else (no rates, no bank details) is ever sent to the model.

## Deliberately not built (yet)

Web page for reviews; tracking consultant/vendor payments; reminders to anyone; combining several consultants on one invoice; vendor bills in QuickBooks; QuickBooks webhooks; running serverless (it would need a managed database and object storage in place of SQLite and the data folder; decision 22); anything to do with recruiting.
