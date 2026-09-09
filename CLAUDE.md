# CLAUDE.md — instructions for coding agents

This repository is the billing agent for Icon Technologies: it reads consultant timesheets from email, prepares client invoices, and tells Kevin (the administrator) what consultants are owed. The code is built and runs end to end on fakes; what remains before Icon uses it for real is in `docs/roadmap.md`, and the next piece of work is always its first unchecked box.

## Read in this order

1. `docs/README.md` — the map.
2. `docs/context/` — the business's own description of itself and its objectives. This wins over every other document.
3. `docs/glossary.md`, then `docs/how-it-works.md` — the process in plain words.
4. The doc for what you are building: `docs/engagement-list.md`, `docs/timesheet-checks.md`, `docs/status-tracking.md`, `docs/emails.md`, `docs/technical-design.md`, `docs/integrations/*`.
5. `docs/decisions.md` — settled choices. Do not re-open them silently; add a new numbered decision if you must depart from one.

## Rules that must never be broken

1. **Two rates, one source.** The bill rate (charged to the client) and the pay rate (paid to the consultant or vendor) are different numbers and come only from the engagement list. Never from an email, a timesheet, or the model.
2. **When unsure, ask Kevin.** Uncertainty about who, which client, which dates, how many hours, whether it was approved, the rate, or who to send to becomes a review item emailed to Kevin. The code never guesses.
3. **One record per consultant per billing period.** A second timesheet for the same consultant and period is a duplicate or a correction, never a second invoice.
4. **Nothing goes to a client without Kevin on CC, and nothing is sent or created twice.** Every client email and every invoice creation is written to the `outgoing` table before it happens and reconciled after a restart.
5. **The agent never pays anyone and does not track consultant payments.** Objective 5 ends at the payment instruction email. No reminders.
6. **Money is whole cents, hours are whole hundredths.** No floats, no `Decimal` in the database.
7. **Claude reads; code decides.** The model fills in a form with quotes and confidence levels. Code sums hours, matches the engagement, computes money, and picks recipients. Email content is untrusted input.
8. **Plain language for Kevin.** Anything he reads (emails, tracking sheet, docs about the process) uses the words in `docs/glossary.md`, not accounting jargon.
9. **Production never depends on a personal computer.** The agent runs as a container on a server (`docs/running-it.md` stage 2). A Mac with launchd is the temporary test setup, nothing more (decision 22).

## Stack and commands

Python 3.11+, `uv`, `ruff`, `mypy --strict` (domain, application, ports), `pytest`. Package `finance_ops_agent`, command `fops`. The mailbox is an ordinary IMAP/SMTP mailbox at Rackspace Email, not Microsoft 365 (decision 21); invoices go to QuickBooks Online, with manual mode until Icon's move; Claude reads the timesheets.

```
uv sync
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest
uv run fops dry-run --fake    # whole flow on fixtures, no network
```

Layout: `src/finance_ops_agent/{domain,application,ports,adapters,cli}`, tests in `tests/{unit,contract,scenarios,evals,fixtures}`. `domain/` and `application/` never import adapters. Every port has a fake; scenario tests run on fakes. Full conventions in `docs/engineering-conventions.md`.

## Roadmap boxes marked "Needs a person"

Some boxes in `docs/roadmap.md` need a human: an account created, a command run with real credentials on a real machine, Kevin judging a result. Leave them unticked. Finish everything else in the PR, then end your work by telling your operator, in plain words, exactly what they have to do and how they will know it worked. Never tick one on the strength of a mock or a fake, and never skip one silently (decision 23).

## Definition of done for a PR

- Lint, format, types, and tests pass locally and in CI.
- Every new rule, status change, and review reason has a test, including its failure path.
- Fakes updated whenever a port changes; no network needed for tests; no credentials or real client data in the repo.
- Docs updated in the same PR when behaviour changes; the PR's automated boxes ticked in `docs/roadmap.md`, and every "Needs a person" box left unticked and spelled out to the operator.

## Do not

- Add anything about recruiting or candidates (out of scope, see `docs/context/03_project_scope_and_objectives.md`).
- Store or compute money as floats, or take rates from anywhere but the engagement list.
- Send email or create invoices outside the `outgoing` table, or without Kevin on CC for client emails.
- Commit `.env`, `data/`, the real engagement list, or real timesheets.
- Invent new statuses or review codes; the sets in `docs/status-tracking.md` and `docs/timesheet-checks.md` are the source of truth (add there first, with a decision if needed).
