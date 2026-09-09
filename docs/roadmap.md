# Roadmap

Work is done in small pull requests in this order. Each PR ticks its boxes here and updates any doc whose behaviour it changes. PRs 2–7 need no credentials and run entirely on fakes and fixtures.

## PR 1 — Documentation (this one)

- [x] Business context, process description, and technical plan committed.

## PR 2 — Scaffold

Set up `uv`, the `src/finance_ops_agent` layout, `ruff`, `mypy`, `pytest`, GitHub Actions, and the `fops` command.

- [x] `uv sync && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest` pass locally and in CI.
- [x] `uv run fops --version` prints a version.
- [x] `engineering-conventions.md` matches what was actually set up.

## PR 3 — Core rules (no I/O)

Money as cents and hours as hundredths; billing periods for each schedule (clipped to engagement dates); invoice and pay math; the statuses and allowed status changes; the review codes; the `TimesheetReading` form.

- [x] The worked example (156 h, $140, $100 → $21,840 and $15,600) is a test.
- [x] Period generation is tested for monthly, twice a month, every two weeks, and weekly, including a first and last partial period.
- [x] Every allowed status change has a test and every disallowed one raises.
- [x] Rounding is tested with property-based tests (Hypothesis).

## PR 4 — Engagement list, database, tracking sheet

Read `engagements.xlsx` (and CSVs) with validation; SQLite tables and migrations; the audit log; write `tracking.xlsx`.

- [x] A sample workbook loads; each bad row described in `engagement-list.md` produces `LIST_ROW_PROBLEM` naming the sheet and row.
- [x] The unique key (consultant, client, period) is enforced by the database.
- [x] Every status change writes an audit row in the same transaction.
- [x] `tracking.xlsx` is written with the columns in `status-tracking.md`.

## PR 5 — The run, on fakes

Fake mailbox from `.eml` files, fake reader scripted per attachment, the checks in order, duplicates, corrections, waiting for weekly timesheets to cover a month, expected items and `waiting_for_timesheet`, `fops dry-run --fake`, `fops status`.

- [x] Scenario tests: happy path; same file twice; same file forwarded by someone else; corrected timesheet before and after "sent"; unknown sender; no approval; daily hours not adding up; dates not matching the schedule; weekly timesheets into a monthly invoice; rate change mid-period.
- [x] Running the same mailbox twice changes nothing the second time.
- [x] Stopping the run half-way and starting again produces no duplicate items or emails.

## PR 6 — Claude reader and test set

The three Claude calls, prompt files with versions, attachment conversion (PDF, images, spreadsheets), quote checking against the PDF text, saved readings, `fops eval`.

- [x] 40+ made-up timesheets with expected readings; `fops eval` reports per-field accuracy and hours error; thresholds recorded.
- [x] CI runs the reader against recorded responses only; the live run is documented.
- [x] `refusal` and `max_tokens` produce `CANT_READ_ATTACHMENT`, never a partial reading.

## PR 7 — Emails and invoices, on fakes

All email templates from `emails.md`; reply handling (approve/cancel by code, review answers via Claude); the invoice PDF and manual-mode numbering; the payment instruction; the Monday summary; the `outgoing` table with draft-then-send and restart reconciliation.

- [x] Snapshot test for every email.
- [x] `fops dry-run --fake` shows, for a fixture mailbox, every email that would be sent.
- [x] A simulated crash between "about to send" and "sent" results in exactly one email after restart.
- [x] The pay rate never appears in client emails and the bill rate never in the payment instruction (tested).

## PR 8 — Microsoft 365

The real mailbox adapter, `fops doctor`, and the setup checklist in `integrations/microsoft-365-email.md`, then dry run mode on the real mailbox.

- [x] Recorded-response tests for delta paging and expiry, attachment download, draft + attachments + send, 429 backoff, and reconciling an in-flight draft.
- [x] `fops doctor` proves the app can read only the agent mailbox.
- [ ] The agent runs in dry run against the real mailbox for at least one billing cycle and Kevin confirms the readings match what he did by hand. *(Needs Icon's tenant: the app registration, admin consent, and the application access policy from `integrations/microsoft-365-email.md`, then `fops doctor`. Code-complete; waiting on credentials and a cycle of real mail.)*

## PR 9 — QuickBooks Online

`fops qbo-connect`, token storage with rotation, customer and item lookup, invoice create with the total check, PDF fetch, daily paid check, void; sandbox first; then ask first mode live.

- [x] Recorded tests including a mismatched total (voided and reported) and a rotated refresh token.
- [x] After a simulated crash the agent finds the invoice it already created by its private note and does not create another.
- [ ] Ask first mode runs live with the sandbox, then with the real company. *(Needs an Intuit app and a sandbox company, then `fops qbo-connect`. Code-complete; waiting on credentials — and on Icon's Desktop → Online move for the real company.)*

## PR 10 — Running it day to day

Scheduler instructions (launchd on the Mac this runs on, and systemd for a Linux box), the lock file, JSON logs, `fops backup` and a tested restore, token-expiry warnings, the automatic-mode guardrails.

- [ ] Two overlapping runs cannot happen.
- [ ] Backup and restore round-trip a populated data folder.
- [ ] Every guardrail in `technical-design.md` has a test; `FOPS_MODE=dry_run` overrides everything.

## Later, if wanted

Tracking consultant and vendor payments and sending reminders (out of scope for now); one invoice covering several consultants; vendor bills in QuickBooks; QuickBooks webhooks instead of the daily check; a web page for reviews; special handling for particular time systems.
