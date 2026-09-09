# Icon Technologies billing agent

An email-based assistant for Icon Technologies, a small IT consulting and staffing business. Consultants email their client-approved timesheets to the agent's mailbox. The agent reads each timesheet, checks it against the engagement list Kevin keeps in Excel, prepares the client invoice (approved hours × bill rate), emails the invoice and timesheet to the client with Kevin on CC, and emails Kevin what the consultant is owed (approved hours × pay rate) so he can pay as he does today. When anything is unclear, it emails Kevin instead of guessing.

Today all of this is done by hand from emails, Excel, and QuickBooks. The goal is that Kevin only has to step in when something needs a decision.

**Status: built, not yet used for real.** The whole flow runs end to end on fakes and fixtures with no network, and the mailbox adapter (plain IMAP and SMTP, for Icon's mailbox at Rackspace Email) is tested against a local mail server. Before Icon can use it: the agent's own mailbox has to exist at Rackspace; the timesheet reader has to be scored against the real model and real timesheets; then one billing cycle in dry run on a Mac; then a move to a server so nothing depends on anyone's computer; then ask-first mode for real. The order, and every step that needs a person rather than code, is in [`docs/roadmap.md`](docs/roadmap.md).

Start with [`docs/README.md`](docs/README.md); coding agents should read [`CLAUDE.md`](CLAUDE.md) first. [`docs/running-it.md`](docs/running-it.md) is how it is installed and run day to day.

## The happy path

1. A consultant emails an approved timesheet.
2. The agent recognises it and reads it.
3. It pulls out the consultant, client, dates, approved hours, and approval.
4. It checks the timesheet (see [`docs/timesheet-checks.md`](docs/timesheet-checks.md)).
5. It matches the consultant to the right client and rates in the engagement list.
6. It prepares the client invoice.
7. It prepares and sends the billing email, with Kevin on CC.
8. It works out what the consultant is owed and emails Kevin.
9. It records the status in its tracking sheet.
10. Kevin steps in only when an approval or a review is needed.

## The one rule everyone must know

There are always two different rates, and they come only from the engagement list:

| | |
|---|---|
| Approved hours | 156 |
| Bill rate (what Icon charges the client) | $140/hour → invoice **$21,840** |
| Pay rate (what Icon pays the consultant) | $100/hour → owed **$15,600** |

The difference is Icon's margin. The two rates are never mixed up, and neither is ever taken from an email.

## Not part of this project

- Recruiting: finding, contacting, screening, or scheduling candidates.
- Paying anyone. The agent tells Kevin what is owed; Kevin pays.
- Tracking whether consultants were paid, or sending reminders to anyone.

## Stack

Python 3.11+ with `uv`, `ruff`, `mypy`, and `pytest`; an ordinary IMAP/SMTP mailbox at Rackspace Email; QuickBooks Online for invoices (manual entry into QuickBooks Desktop until the move); Claude for reading timesheets; in production, a container on a small Linux server with a heartbeat and off-machine backups. Details in [`docs/technical-design.md`](docs/technical-design.md).

```
uv sync
uv run fops dry-run --fake    # the whole flow on fixtures, no network, nothing sent
uv run fops doctor            # every credential and permission; sends nothing to a client
uv run pytest
```

`FOPS_MODE` is `dry_run`, `ask_first`, or `auto`. `dry_run` is the stop button: while it is set, nothing reaches a client and nothing is created in QuickBooks, whatever else it is told.
