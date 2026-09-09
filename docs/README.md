# Documentation map

Read in this order the first time. `context/` is the business's own description of itself and wins over everything else here; the other documents say how the agent behaves and how it is built.

## The business (source of truth)

- `context/README.md` — what the context folder is.
- `context/01_business_context.md` — what Icon Technologies does.
- `context/02_current_business_process.md` — how the work is done by hand today.
- `context/03_project_scope_and_objectives.md` — what the agent must do (the five objectives) and what it must not do.
- `context/04_business_rules_and_edge_cases.md` — the rules and the awkward cases.
- `context/05_data_and_entities.md` — the things the process deals with.

## How the agent behaves (plain language; Kevin can read these)

- `glossary.md` — the words used everywhere else.
- `how-it-works.md` — the whole process, step by step, and the three modes.
- `engagement-list.md` — the spreadsheet Kevin keeps; the only source of rates and contacts.
- `timesheet-checks.md` — what the agent reads from a timesheet, the checks, and every reason it asks Kevin for a review.
- `status-tracking.md` — the statuses, duplicates, corrections, and the tracking sheet.
- `emails.md` — every email the agent sends and how Kevin replies.
- `running-it.md` — installing it: the temporary Mac test setup and the server it runs on for real, the schedule, logs, backups, the heartbeat, and what to do when something looks wrong.
- `first-cycle.md` — the tally sheet for the first real billing cycles (dry run, then ask first): what arrived, what was right, what needed Kevin.
- `open-questions.md` — things only Kevin can answer, with the defaults used meanwhile.

## How it is built (for coding agents)

- `technical-design.md` — components, ports and adapters, database, money, never-twice rules, modes, configuration, running it.
- `engineering-conventions.md` — tooling, layout, naming, tests, definition of done.
- `integrations/email-imap-smtp.md` — the mailbox at Rackspace Email: setup, reading (IMAP), sending (SMTP), never twice, testing.
- `integrations/quickbooks-online.md` — invoices: manual mode now, QuickBooks Online later.
- `integrations/claude-extraction.md` — reading timesheets and replies with Claude.
- `decisions.md` — settled choices and why.
- `roadmap.md` — the PRs, in order, with what "done" means for each.

Repository root: `README.md` (overview), `CLAUDE.md` (instructions for coding agents), `AGENTS.md` (pointer for other tools).
