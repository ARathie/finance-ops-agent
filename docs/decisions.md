# Decisions

Settled choices, with the reason. A new PR that wants to depart from one of these adds a new numbered entry explaining why, in the same PR; it does not silently diverge.

## 1. Python 3.11+, uv, ruff, mypy, pytest

Python has the best tooling for reading PDFs, spreadsheets, and images and for the Anthropic SDK; `uv` keeps installs fast and reproducible. Consequence: one `pyproject.toml`, `src/` layout, CI runs the four tools.

## 2. Plain-language, email-first design

Kevin runs the business from his inbox and Excel; nothing in this project should require him to learn a program or new vocabulary. Consequence: every interaction with Kevin is an email he can reply to; documents about the process avoid accounting jargon; the operator command line is for developers only.

## 3. The engagement list is an Excel workbook Kevin owns

Rates, contacts, and terms already live in spreadsheets today. The agent reads the workbook every run and never writes to it. Consequence: no admin screens; row-level problems become review emails; the agent snapshots the row it used onto each item so a later edit never changes an invoice already prepared.

## 4. Rates never come from emails or timesheets

Bill rate and pay rate come only from the engagement list. A rate mentioned in an email is ignored. Consequence: the model is never shown rates and the checks never read them from a document.

## 5. One record per consultant per billing period

The unique key (consultant, client, period) is what prevents duplicate invoices. A second timesheet for the same key is a duplicate or a correction, never a new invoice. Consequence: weekly timesheets for a monthly engagement are collected under one record.

## 6. Ports and adapters, with fakes as first-class code

The mailbox (IMAP and SMTP), QuickBooks Online, Claude, Excel, and the database sit behind small interfaces with fake versions. Consequence: the whole flow runs and is tested with no network; adapters are swapped by settings.

## 7. SQLite plus a tracking spreadsheet

One database file is enough for a dozen consultants and one operator, and is easy to back up. Money is stored as whole cents and hours as hundredths (SQLite would turn decimals into floats). The tracking sheet is rewritten from the database for Kevin's records. Consequence: no database server; backups are a folder copy.

## 8. Approval before anything is created or sent

QuickBooks Online has no draft invoices, and a sent email cannot be unsent. So the agent asks Kevin (ask first) or checks the guardrails (automatic) before it creates the invoice or sends the billing email. Consequence: the "approve this invoice?" email and the `outgoing` table.

## 9. Approval and review answers by email reply, from Kevin only

A reply is the simplest possible action for Kevin. Only replies from `kevin@icon-technologies.com` in the original thread are accepted; approvals must start with `approve` or `cancel`. Consequence: no web page; the spoofing risk is limited to someone who can already send mail as Kevin.

## 10. Three modes: dry run, ask first, automatic

Trust is earned gradually. Dry run proves the readings against what Kevin does by hand; ask first puts him in the loop for every invoice; automatic is switched on per engagement. Consequence: one setting plus one column in the engagement list; `dry_run` is the kill switch.

## 11. QuickBooks Online is the target; manual mode until then

The move from QuickBooks Desktop is planned but not done. The agent works fully in manual mode (it numbers and renders the invoice, Kevin types it into QuickBooks) so the project is useful now, and switches to QBO with a setting. Consequence: the `AccountingSystem` port has two real adapters.

## 12. The agent never pays anyone and does not track consultant payments

Objective 5 ends at the payment instruction email. Paying, and knowing whether it was paid, stays with Kevin. Consequence: no bank or payroll integration, no reminders, no "paid" status for consultants.

## 13. Microsoft Graph with a small hand-written client (superseded by 21)

Superseded: Icon's email is not on Microsoft 365. Kept for the record. `msal` for tokens and `httpx` for a handful of endpoints, restricted by an application access policy to the agent mailbox. Consequence: easy to record and replay in tests; the app cannot read other mailboxes.

## 14. Claude reads; code decides

The model fills in a fixed form with quotes and confidence levels. Code sums hours, matches the engagement, computes money, and picks recipients. Consequence: readings are testable against a made-up timesheet set; a model change is a measured change.

## 15. Nothing is sent twice

Every email to a client and every invoice creation is written down before it happens, with an id, and reconciled with the provider after a restart before any retry. Consequence: the `outgoing` table and the draft-then-send pattern.

## 16. Invoice PDFs are rendered by a small built-in writer, not WeasyPrint or ReportLab

`technical-design.md` left the choice open ("HTML template + WeasyPrint (or ReportLab)"). The invoice is one page of plain lines — consultant, role, period, hours, rate, total — so a ~60-line writer that emits a one-page Helvetica PDF covers it with no third-party renderer and no system libraries. That matters for the machine this runs on: WeasyPrint needs GTK/Pango via Homebrew on macOS, which a `brew upgrade` can break on an unattended machine months later. Consequence: `adapters/pdf/writer.py` is both the real renderer and the fake, output is byte-for-byte deterministic (so snapshot tests are meaningful), and `pypdf` can read every line back out. If invoices ever need logos, tables, or styling, revisit this with a new decision — ReportLab is the pure-Python next step.

## 17. Kevin's replies are matched by the subject of the email he replies to

`technical-design.md` says replies are matched by conversation id *and* an item reference in the subject. On fakes there is no provider conversation id yet, so the agent matches the reply to the exact subject line of the email it answers, and the outgoing table holds those subjects. Consequence: reply handling is fully testable now; when the real mailbox arrives (PR 8) the conversation id becomes the primary key for matching and the subject stays as the fallback.

## 18. The application records the invoice, not the accounting adapter

An `AccountingSystem` adapter creates the invoice in the accounting system and returns its number, external id, and PDF; writing the agent's own `invoices` row is the application's job. The first cut had manual mode write that row as a side effect, which silently left QuickBooks Online mode with no invoice rows at all — so the tracking sheet, the daily paid check, and replacement invoices would all have quietly stopped working once the mode changed. Consequence: `application/outgoing.py` writes the row once for whichever adapter made the invoice, and `create_invoice` stays idempotent per item so a crash mid-run cannot produce a second one (in QuickBooks by recognising the item id in the invoice's private note).

## 19. One run at a time by advisory file lock, not a pid file

A run takes a POSIX advisory lock (`fcntl.flock`) on `data/run.lock`; a run that cannot get it stops and leaves the work to the next one. A pid file cannot promise the same thing: a killed or crashed run leaves the file behind, and the next run has to guess whether the pid it names is still the same program. The kernel releases an advisory lock when the process ends, however it ends. Consequence: no stale-lock cleanup, and the agent runs on macOS and Linux only — which matches the machine it runs on (`open-questions.md`) but would need rewriting for Windows.

## 20. `dry_run` wins over any command-line flag

`FOPS_MODE=dry_run` is the stop button, so `fops run --mode auto` still runs as a dry run and says so; a flag may only lower a live mode to `dry_run`, never raise one. A kill switch that a flag can argue with is not a kill switch, and the flag is the easiest thing to get wrong in a scheduler file nobody rereads. Consequence: changing what the agent may send is a deliberate change to `.env`, and `effective_mode` is one small function with the whole matrix under test.

## 21. The mailbox is IMAP and SMTP at Rackspace Email, not Microsoft 365

The first planning conversation assumed Microsoft 365, and PR 8 built a Microsoft Graph adapter on that assumption. Kevin's account settings show the truth: Icon's email is hosted at Rackspace Email (`secure.emailsrvr.com`, IMAP on 993, SMTP on 465, password login). Decision: the real mailbox adapter speaks plain IMAP and SMTP (`adapters/email/`, per `integrations/email-imap-smtp.md`); the Graph adapter, its fixtures and tests, and the `msal` dependency are removed in PR 11 rather than kept as a second option nobody uses. Consequences: setup is a mailbox and a password in the Rackspace control panel, with no app registration, admin consent, or access policy; the credentials reach one mailbox by construction; message identity is the `Message-ID` header plus a content hash instead of provider ids; "never twice" for sending rests on a self-made Message-ID recorded before the send, the Sent folder for reconcile, and, in the rare case the process dies between the server accepting the email and the agent noting it, asking Kevin (`SEND_UNCERTAIN`) instead of guessing; Kevin's own mail program is irrelevant. Supersedes decision 13.

## 22. Production runs on a server, never on someone's computer

The Mac with launchd (PR 10) is fine for the first real-life test and wrong for production: it stops when the Mac sleeps, logs out, or leaves the building, and nobody is told. Decision: the agent is a real service. It ships as a container image built by CI, runs `fops serve` (a run every 15 minutes, a backup nightly, a heartbeat after each) on a small always-on Linux server or any container host with a persistent disk, keeps its data on a mounted volume, copies backups to object storage off the machine, and checks in with a heartbeat service that emails Ash and Kevin when the check-ins stop. Serverless was considered and deferred: SQLite and a folder of files are exactly right for a dozen consultants and one operator, and a serverless platform would force a managed database, object storage for every file, and a secrets service in exchange for nothing this workload needs; the `Store` port keeps that door open (a Postgres adapter) if the picture changes. Consequences: a `Dockerfile`, `docker-compose.yml`, `fops serve`, `FOPS_HEARTBEAT_URL`, and `FOPS_BACKUP_TARGET` (PR 14); `running-it.md` has a temporary stage 1 (Mac) and a permanent stage 2 (server); the agent listens on no port and needs no inbound firewall rule.

## 23. Roadmap boxes that need a person are marked, never faked

Some steps cannot be verified by a test: creating the mailbox, running the doctor with real credentials, Kevin judging a cycle of readings. Decision: such boxes carry **Needs a person** in `roadmap.md`. A coding agent leaves them unticked and ends its work by telling its operator exactly what to do and how they will know it worked; it never ticks one on the strength of a mock, and never skips one silently. Consequences: the legend at the top of `roadmap.md`, a rule in `CLAUDE.md`, and PRs 11 to 17 written with the split between automated and human verification made explicit.

## 24. Timesheets are weekly, and a part week is Kevin's to settle

Icon's timesheets list hours **per week**, not per day: one row per week against a printed date, with a `State` column showing the client's approval and the approver's name. Daily entries are the exception, so the reading form carries `row_entries` as well as `daily_entries` and the prompt asks for the weekly rows exactly as printed.

A week at either end of a month covers days on both sides of it. Its hours are therefore **not** all billable in the period, and how they split is not something the model may guess and not something code may assume. Code never apportions a straddling week by spreading it over working days: on the July sample that computes 19.2 hours where the vendor's invoice says 16, which would overbill the client.

So the hours to bill come, in order:

1. the total printed on the document — a vendor invoice prints one (`176 hours * $110`);
2. the daily entries, summed by code, when the timesheet gives days;
3. the weekly rows, summed by code, but **only when every row falls inside the period**.

When none of those holds and a row straddles the period, the item becomes a `PART_WEEK_UNCLEAR` review and Kevin replies with the hours for that week. That is decision 14 and the "ask Kevin" rule applied to the one number nobody has written down.

An email may carry the approved timesheet **and** the consultant's vendor company's invoice for the same hours, or the timesheet alone when there is no vendor. The timesheet is the proof of the hours and the approval. The rate printed on a vendor invoice is never used for anything (decision 4); Kevin compares it with the payment instruction himself.


## 25. For the first cycle, timesheets are forwarded by hand from one named address

The first real-life test (PR 13) cannot start by telling every consultant to email the agent. The timesheets that exist are the ones already sitting in Kevin's mailbox, and they are tested by forwarding them. A forward arrives from the person forwarding it, not from the consultant, so the sender no longer says whose timesheet it is.

Decision: a setting, `FOPS_TIMESHEET_FORWARDERS`, lists addresses allowed to send in a timesheet that is not their own. Mail from such an address is read as a timesheet; the consultant is then taken from the document, which is the fallback `match_consultant` already had for a consultant writing from a new address. Nothing about rates, approval, or the checks changes: a forwarded timesheet is checked exactly like a direct one, and an unrecognised name on it is still `CONSULTANT_UNKNOWN` rather than a guess.

Three limits keep this from leaking into real running:

- **Kevin's address can never be a forwarder.** Everything from `FOPS_ADMIN_EMAIL` is read as a reply, and approvals travel that path (rule 4). `Config.from_env` refuses the setting rather than letting a test put the approval rule at risk.
- **It is a setting, not a column in the engagement list.** Kevin's file describes Icon's business and outlives the test; this is scaffolding for one machine and one cycle, and going live is deleting a line from `.env`.
- **`fops doctor` always says who may forward**, and fails the check outright if the mode is anything but `dry_run`.

This is why the roadmap splits its last mailbox box and PR 13's cycle boxes in two: a forwarded-timesheet box that can be ticked during the test, and a separate box for a timesheet arriving straight from a consultant, which is what "used in real life" actually requires.
