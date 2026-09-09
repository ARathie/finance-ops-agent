# Roadmap

Work is done in small pull requests in this order. Each PR ticks its boxes here and updates any doc whose behaviour it changes.

## How to read the boxes

- `[x]` done and verified. `[ ]` not done.
- **Needs a person** marks a box a coding agent cannot tick. Someone has to create an account, run a command on a real machine with real credentials, or judge a result. A coding agent that finishes the code for a PR with such boxes leaves them unticked and ends its work by telling its operator, in plain words, exactly what to do and how they will know it worked (decision 23). Never tick one on the strength of a mock or a fake, and never skip one silently.
- Every other box is verified by tests or commands that run with no credentials and no network, and a coding agent ticks it only after running them.

## Where things stand

PRs 1–7 and 10 are done. PR 8 built a Microsoft 365 adapter on a wrong assumption: Icon's email is hosted at Rackspace Email and is spoken to with IMAP and SMTP (decision 21), so PR 11 replaces it. PR 9's live QuickBooks box moved to PR 16. Production must never depend on someone's personal computer (decision 22): the Mac is for the first real-life test only (PR 13), and PR 14 moves the agent to a server.

Order: 11 → 12 → 13 → 14 → 15 → 16 → 17. PRs 11 and 12 can be worked at the same time. The project has succeeded at PR 15: Icon invoicing through the agent for real.

## PR 1 — Documentation

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

The recorded answers are bootstrap copies of the expected answers until PR 12 replaces them with the model's real ones; the 100% the eval reports today proves the harness, not the reading.

## PR 7 — Emails and invoices, on fakes

All email templates from `emails.md`; reply handling (approve/cancel by code, review answers via Claude); the invoice PDF and manual-mode numbering; the payment instruction; the Monday summary; the `outgoing` table with write-down-then-send and restart reconciliation.

- [x] Snapshot test for every email.
- [x] `fops dry-run --fake` shows, for a fixture mailbox, every email that would be sent.
- [x] A simulated crash between "about to send" and "sent" results in exactly one email after restart.
- [x] The pay rate never appears in client emails and the bill rate never in the payment instruction (tested).

## PR 8 — Microsoft 365 (superseded by PR 11)

Built a Microsoft Graph mailbox adapter, `fops doctor`, `fops run`, and the config loading. Icon's mail is not on Microsoft 365 (decision 21), so the adapter is replaced by PR 11 and this PR's last box is void. The doctor command, the real-run wiring, the config loading, and the recorded-response test pattern carry over.

- [x] Recorded-response tests for delta paging and expiry, attachment download, draft + attachments + send, 429 backoff, and reconciling an in-flight draft.
- [x] `fops doctor` proves the app can read only the agent mailbox.
- ~~The agent runs in dry run against the real mailbox for at least one billing cycle~~ → moved to PR 13, against the real mailbox at Rackspace.

## PR 9 — QuickBooks Online

`fops qbo-connect`, token storage with rotation, customer and item lookup, invoice create with the total check, PDF fetch, daily paid check, void; sandbox first.

- [x] Recorded tests including a mismatched total (voided and reported) and a rotated refresh token.
- [x] After a simulated crash the agent finds the invoice it already created by its private note and does not create another.
- ~~Ask first mode runs live with the sandbox, then with the real company~~ → moved to PR 16.

## PR 10 — Running it day to day

The lock file, JSON logs, `fops backup` and a tested restore, token-expiry warnings, the automatic-mode guardrails, and the operator's page `running-it.md`. The launchd setup it documented is now the stage-1 test setup only; production is PR 14.

- [x] Two overlapping runs cannot happen.
- [x] Backup and restore round-trip a populated data folder.
- [x] Every guardrail in `technical-design.md` has a test; `FOPS_MODE=dry_run` overrides everything.

## PR 11 — The real mailbox: IMAP and SMTP at Rackspace Email

Replace the Microsoft Graph adapter with an IMAP/SMTP adapter per `integrations/email-imap-smtp.md`: `adapters/email/` (reading, sending, folders), the `MAIL_*` settings, the mailbox checks in `fops doctor`, the `SEND_UNCERTAIN` review (added to `timesheet-checks.md`), reply matching by `In-Reply-To`, and `.env.example` committed (the ignore rule that swallowed it is fixed). Delete `adapters/microsoft365/`, `tests/fixtures/graph/`, the Graph contract tests and recorder, and the `msal` dependency. The `EmailInbox` and `EmailSender` ports change shape (no draft ids); fakes and contract tests updated.

Done when:

- [ ] A local mail server runs in CI (GreenMail as a service container, or Dovecot) and the adapter passes the port contract suite against it, the same suite the fakes pass.
- [ ] Reading: new mail by UID; a `UIDVALIDITY` change → full rescan with zero duplicate `messages` rows; a redelivered message skipped by Message-ID; a message without a Message-ID gets a synthetic one; inline images ignored unless alone; `Agent/…` folders created with either delimiter; move by `MOVE` and by copy-delete.
- [ ] Sending: the Message-ID is stored before the send; `accepted_at` recorded after `250`; a copy in Sent; reconcile → found, too young, and `SEND_UNCERTAIN`, each tested with a killed process; "resend" reuses the Message-ID; a rejected recipient → `SEND_FAILED`; a test asserts TLS verification is on.
- [ ] Kevin's replies matched by `In-Reply-To`, then by subject.
- [ ] `MAIL_START_DATE` respected; `fops doctor` output tested with and without settings; the scenario suite is unchanged and green on the fakes.
- [ ] `grep -ri "microsoft\|graph\|msal\|entra" src tests docs` finds nothing except this roadmap and decision 21.
- [ ] `.env.example` is committed and lists every setting in `technical-design.md`.
- [ ] **Needs a person:** the agent mailbox exists at Rackspace Email; its address and password are in `.env` on the machine that will run the test; the name of whoever holds Icon's Rackspace admin login is written in `open-questions.md`.
- [ ] **Needs a person:** `uv run fops doctor --send-test-email` passes every line, Kevin receives the test email, and the copy appears in the agent mailbox's Sent folder.
- [ ] **Needs a person:** one real email with a timesheet attached, sent from a consultant address that is on the engagement list, is picked up by `uv run fops dry-run` (real mailbox, dry run) and produces the "timesheet received" email to Kevin.

## PR 12 — Prove the reader on real timesheets

The eval set's recorded answers are still bootstrap copies of the expected answers, so the 100% it reports proves the harness, not the model. This PR makes the numbers real and adds Icon's actual timesheet formats. It can be worked alongside PR 11.

Code: `fops eval --live` writes the model's real answers; `thresholds.json` gains `source`, `model`, `prompt_version`, and `recorded_on`; a test fails while `source` is still `bootstrap`; anonymised real samples added under `tests/evals/timesheets/` (names, rates, and client names replaced; nothing real); prompt fixes for whatever the live run gets wrong; a note in `integrations/claude-extraction.md` on the cost per timesheet from the run's token counts.

Done when:

- [ ] `tests/evals/thresholds.json` says `"source": "live"` and CI fails if it does not.
- [ ] CI replays the live-recorded answers and the scores meet the thresholds.
- [ ] At least one case per client time system Icon actually uses (see `open-questions.md`) is in the set, anonymised, and a test lists them.
- [ ] **Needs a person:** an `ANTHROPIC_API_KEY` is provided and `uv run fops eval --live` is run (it costs a few dollars); the recorded answers it writes are committed.
- [ ] **Needs a person:** Kevin supplies at least one real timesheet per client system with names and rates changed; the person running this confirms nothing identifying remains before committing.
- [ ] **Needs a person:** someone reads the live scores and decides they are good enough to go to PR 13. The bar to argue for: hours and approval read correctly on every real sample, or wrong in a way the checks catch as a review. The decision and the numbers are recorded in `integrations/claude-extraction.md`.

## PR 13 — The first real-life test: one billing cycle in dry run, on a Mac

Almost entirely a person's work. The agent reads Icon's real mailbox for one full billing cycle in `dry_run` mode; nothing goes to a client; Kevin compares every "timesheet received" and "would invoice" email to what he did by hand. `running-it.md` stage 1 is the checklist and `first-cycle.md` is the tally sheet.

Code: a starter workbook `templates/engagements-template.xlsx` with the exact columns and one made-up example row per sheet; `fops doctor` prints the mode and the start date so nobody runs live by accident; whatever the first cycle turns up.

Done when:

- [ ] The starter workbook loads through the engagement list reader with zero problems, and a test proves it.
- [ ] **Needs a person:** Kevin fills the engagement list (every active client, consultant, vendor, engagement, and rate) and answers the timezone question; `fops doctor` shows zero engagement-list problems.
- [ ] **Needs a person:** Kevin tells consultants to send timesheets to the agent's address (as well as, or instead of, his own).
- [ ] **Needs a person:** the Mac is set up per `running-it.md` stage 1 with `FOPS_MODE=dry_run`, and `fops doctor` passes.
- [ ] **Needs a person:** for one whole billing cycle, every timesheet produced a "timesheet received" email and either a "would invoice" preview or a review; Kevin confirms each preview's client, consultant, period, hours, and amount against his own invoice.
- [ ] **Needs a person:** Kevin answered at least one review by replying, and the agent applied the answer.
- [ ] **Needs a person:** the cycle-1 table in `first-cycle.md` is filled in. Exit rule: zero previews with a wrong amount that were not also a review.

## PR 14 — Production: a server, not a laptop

Per decision 22 and `running-it.md` stage 2. The agent runs as a container on a small always-on Linux server (or any container host with a persistent disk), depends on no personal computer, and someone is told when it stops.

Code: a `Dockerfile` (python 3.11-slim, `uv sync --locked --no-dev`, a non-root user, `fops serve` as the default command) and a CI job that builds it and publishes it to GitHub Container Registry on a version tag; `fops serve` (one process: a run every `FOPS_RUN_EVERY_MINUTES`, `fops backup` nightly at `FOPS_BACKUP_AT`, a heartbeat ping to `FOPS_HEARTBEAT_URL` after every successful run and backup, a clean stop on `SIGTERM`, the existing lock so nothing overlaps); backup upload to object storage (`FOPS_BACKUP_TARGET`, an rclone remote path) with retention (daily kept 90 days, the first of each month kept forever); `docker-compose.yml` with a named volume for `data/` and `restart: unless-stopped`; log rotation for `data/fops.log`; `fops doctor` also checks the heartbeat URL and the backup target.

Done when:

- [ ] CI builds the image and `docker run <image> fops --version` prints the version; the image contains no `.env` and runs as non-root.
- [ ] `fops serve` with a fake clock runs at the right cadence, backs up once per night, never overlaps runs, pings the heartbeat only after a successful run, and stops within 30 seconds of `SIGTERM` without leaving an `in_flight` row (tested).
- [ ] Backup upload and retention are tested against a local stand-in (MinIO in CI, or a temporary directory as the rclone remote).
- [ ] `running-it.md` stage 2 matches what was built, command for command.
- [ ] **Needs a person:** a server is chosen and provisioned (open question: which provider; the default is a 1 vCPU / 1 GB Ubuntu LTS virtual machine at DigitalOcean, Hetzner, or AWS Lightsail), with Docker, unattended security updates, SSH keys only, and no inbound ports besides SSH.
- [ ] **Needs a person:** `.env` placed on the server (root-only), the volume mounted, `docker compose up -d`, and `docker compose exec fops fops doctor` passes every line.
- [ ] **Needs a person:** a heartbeat check exists (default: healthchecks.io, 15-minute period, 45-minute grace) that emails Ash and Kevin; proven by stopping the container for an hour and receiving the alert, then starting it and seeing the alert clear.
- [ ] **Needs a person:** the next morning a backup zip is in object storage; it is restored on a scratch machine with `fops restore`, and `fops status` there shows the same items as the server.
- [ ] **Needs a person:** the Mac job from stage 1 is unloaded so exactly one agent is running, and `fops status` on the server shows it picking up new mail.

## PR 15 — Ask-first mode, live: Icon invoices through the agent

The project's success criterion. `FOPS_MODE=ask_first` on the server; for one full billing cycle every invoice goes out through Kevin's "approve" reply, payment instructions reach Kevin, and the Monday summary arrives.

Code: only fixes for whatever the cycle turns up.

Done when:

- [ ] **Needs a person:** `FOPS_MODE=ask_first` is set on the server and `fops doctor` reports the mode.
- [ ] **Needs a person:** the first "approve this invoice?" email is answered, the client receives the billing email with Kevin on CC and both attachments, and Kevin receives the payment instruction.
- [ ] **Needs a person:** the cycle-2 table in `first-cycle.md` is filled in. Exit rule: zero incorrect invoices sent to a client.
- [ ] **Needs a person:** Kevin says he wants to keep using it. That is the finish line for "used in real life".

## PR 16 — QuickBooks Online, live

The code exists (PR 9). This needs an Intuit developer app and a sandbox company, and then Icon's move from QuickBooks Desktop to Online. Manual mode keeps working meanwhile.

Done when:

- [ ] **Needs a person:** the Intuit app is created; `fops qbo-connect` against the sandbox; `fops doctor` passes the QuickBooks checks; one ask-first invoice is created in the sandbox and its total equals the agent's to the cent.
- [ ] **Needs a person:** after Icon's move to QuickBooks Online: the customer names in QuickBooks match the engagement list's "QuickBooks customer" column; `fops qbo-connect` against the real company; `FOPS_ACCOUNTING=quickbooks`; the first live cycle's invoices are reviewed by Kevin in QuickBooks; the daily paid check marks a real paid invoice `client_paid`.

## PR 17 — Automatic mode, engagement by engagement

The guardrails exist (PR 10). This is the rollout.

Done when:

- [ ] **Needs a person:** after at least three clean ask-first cycles for an engagement, Kevin sets "Send automatically = yes" on its row and `FOPS_MODE=auto` on the server; the next clean timesheet for it goes out without an approval email, with Kevin on CC.
- [ ] **Needs a person:** the stop button is proven live: `FOPS_MODE=dry_run` is set, a timesheet arrives, nothing is sent, Kevin gets the preview; then the mode is restored.

## Later, if wanted

Tracking consultant and vendor payments and sending reminders (out of scope for now); one invoice covering several consultants; vendor bills in QuickBooks; QuickBooks webhooks instead of the daily check; a web page for reviews; special handling for particular time systems; a Postgres `Store` adapter if the agent ever moves to a serverless platform (decision 22).
