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

Microsoft 365, QuickBooks Online, Claude, Excel, and the database sit behind small interfaces with fake versions. Consequence: the whole flow runs and is tested with no network; adapters are swapped by settings.

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

## 13. Microsoft Graph with a small hand-written client

`msal` for tokens and `httpx` for a handful of endpoints, restricted by an application access policy to the agent mailbox. Consequence: easy to record and replay in tests; the app cannot read other mailboxes.

## 14. Claude reads; code decides

The model fills in a fixed form with quotes and confidence levels. Code sums hours, matches the engagement, computes money, and picks recipients. Consequence: readings are testable against a made-up timesheet set; a model change is a measured change.

## 15. Nothing is sent twice

Every email to a client and every invoice creation is written down before it happens, with an id, and reconciled with the provider after a restart before any retry. Consequence: the `outgoing` table and the draft-then-send pattern.
