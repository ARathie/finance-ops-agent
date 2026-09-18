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

## 26. The billed month is stated, not inferred from the timesheet's dates

Decision 24 established that Icon's timesheets are weekly and that a week at either end of a month covers days on both sides of it. What it did not settle is which month such a timesheet is *for*, and the code kept a rule that contradicted it: `fit_billing_period` required the timesheet's whole span to sit inside one billing period. A weekly timesheet never does. A July sheet runs from the week starting 20 June to 31 July; software anchored it to June, found June did not cover 25 July, and raised `PERIOD_MISMATCH`. Every real timesheet failed this way, and none ever reached the hours check.

Two real months from one consultant showed what the documents actually carry:

- The timesheet is an export from the client's time system, one row per week, headed **"Week starts on"**, with a `State` column and the approver's name. It prints the neighbouring month's weeks as well, and says nothing about which month is being billed.
- The vendor's invoice names the month by its date (31/07, 31/08), and its rows carry **only the hours belonging to that month**: the week starting 29 August appears as 8 hours, being the single August weekday in it. Its column is headed "WEEK ENDING" while printing week-starting dates — the timesheet's own heading is the honest one.
- A note added beside the straddling week states its in-month hours: "16 Hours in Jul-26", "8 hours in Aug". Both agree with the invoice.

Decision: **the month comes from what a document states.** `stated_month` is read as its own answer — an invoice's date or period, a heading, a note naming the month. Failing that, the month holding most of the timesheet's days wins; a week's overhang never outvotes the month the sheet is for. The rows are then allowed to overhang the period at both ends, and the period is never trimmed to fit them.

The hours ladder of decision 24 is unchanged: the printed total leads, because the vendor has already apportioned the straddling week. What is added is a check, not a new source of truth. Where a note states a straddling week's in-month hours, code adds it to the weeks wholly inside the period and compares the result with the printed total; a disagreement is `PART_WEEK_DISAGREES` and goes to Kevin rather than being resolved by preferring one document. Where no total is printed, that same sum becomes the total — the one case where the note supplies a number rather than checking one.

Two things stay guarded. A week printed for a neighbouring month is excluded from the sum, because it belongs to another invoice; summing August's visible rows gives 200 or 240 hours against a correct 168. And an overhang is only expected when weekly rows explain it: a timesheet with no rows whose dates run more than a week past the period is still `PERIOD_MISMATCH`, which is what keeps a whole-month timesheet from being billed against a twice-a-month engagement.

## 27. Code counts only the days inside the month it is billing

Decision 26 made the billing period come from what a document states, and made weekly rows outside that period unbillable. It missed the other half: **daily** entries were still summed whole.

The case that found it is a real May 2025 export from a client's time system — five printed pages, one per week, each running Sunday to Saturday. The first page covers Sunday 27 April to Saturday 3 May and holds 40 hours, of which only 16 fall in May. Summed whole, the document reads 192 hours; the right answer is 168. The agent billed 192, and **nothing flagged it**: with 22 weekdays in May, full time is 176 hours, so 192 is 9 per cent over and never reaches the "unusual" threshold. A wrong invoice went out silently, which is the failure this system exists to prevent.

Decision: when a billing period is known, code sums **only the daily entries falling inside it**, exactly as it already does for weekly rows. A week straddling the month end needs no note when the timesheet is daily: the days are dated, so code apportions them exactly.

That also settles a conflict with decision 24, which puts the printed total first. That ordering assumed the total was for the period, which is true of a vendor invoice that bills one month and has already apportioned the straddling week. It is not true of a document that prints a total across days from several months. So: where daily entries extend outside the period, **the dated days win over a printed total**, and a disagreement between them is `HOURS_DONT_ADD_UP` naming the reason. Where the days sit inside the period, decision 24 is unchanged and the printed total still leads.

The principle underneath is the project's first one: Claude reads, code decides. The model reports the days it sees, with their dates. Which of them belong to the month being billed is arithmetic, and arithmetic is never the model's to do.

## 28. A full-time day is eight hours, on days a consultant is expected to work

The old rule questioned hours only when they ran 25 per cent over full time. On a 21-weekday month that is 210 hours against an expected 168 -- a consultant would have to bill six extra weeks before anyone was asked. Icon's engagements are eight hours a day, so anything meaningfully above that is either overtime nobody agreed or a misread document, and both are Kevin's to see. Decision: the threshold is **105 per cent** of what the period's working days come to at eight hours each.

A **working day is a weekday that is not a US federal holiday.** Icon's consultants are not expected to work them, so the week of Memorial Day is a 32-hour week, not a light one, and a month holding two holidays should not be measured against a figure nobody was going to bill. `domain/holidays.py` works the eleven federal holidays out arithmetically -- each is a fixed date or an nth weekday, and a fixed date at a weekend is observed on the nearest weekday -- so there is no data file to go stale.

The three real months are the evidence for the premise. Each comes to exactly 100 per cent of its working days at eight hours: May 2025 is 168 against 21 working days, July 2026 is 176 against 22, August 2026 is 168 against 21. Consultants really are not billing holidays.

This was got wrong once and the eval set caught it. Measured against made-up timesheets that had consultants working every weekday, holidays included, a holiday-aware expectation questioned 13 of 48 cases and dropped review-code accuracy from 100 per cent to 72. The reading to draw was not that the rule was wrong but that **the made-up timesheets were**: nobody works Thanksgiving, so a test set that says they do is testing an invented problem. `build_test_set.py` now puts hours only on working days, and the set was rebuilt. Anyone tempted to relax the threshold because the evals complain should check the fixtures first.

Low hours are still never flagged. A consultant may take leave or work part of a month, and the agent has no business asking about that. If under-reporting is ever worth checking -- "this month looks light, is a timesheet missing?" -- the same working-day count is what it should measure against.


## 29. Invoice numbers are Kevin's format, and the agent assigns them in both modes

Kevin's invoices are numbered `<MMDDYY><client code>-<consultant code>`: `083126MT-PS` is August 2026 for Priya Shah at Mastec. The date is the **end of the billing period**, not the day the invoice was made, so a late August invoice still reads `083126` and the number says which month's work it is for.

Decision: **the agent works the number out, in manual mode and in QuickBooks Online alike**, and QuickBooks is told to use it (`DocNumber`). Previously manual mode counted `ICON-<year>-<number>` from its own counter and QuickBooks Online let QuickBooks number the invoice, which meant numbering changed shape on the day Icon switched. Consequences:

- The Clients sheet gains **Invoice code** and the Consultants sheet **Initials** (`engagement-list.md`). `MT` does not follow from "Mastec" by any rule, so a missing or duplicated code is a `LIST_ROW_PROBLEM` for Kevin, never a guess. Initials the agent can take from the name are taken from it; the column is for the names it cannot.
- Two consultants at one client who share initials both switch to the first initial and the whole last name (`PSHAH`, `PSINGH`). Both change rather than only the newcomer, because a number that depended on who Kevin entered first would be worse than either. It applies only at the client where they clash, and only to invoices not yet sent.
- QuickBooks Online honours `DocNumber` only when **Custom transaction numbers** is on in the company settings. The agent compares the number that comes back with the one it asked for and voids the invoice when they differ, the same way it voids one whose total disagrees: an invoice under a number Kevin did not choose never reaches a client.
- One invoice per consultant per client per month (rule 3) makes the number unique on its own. A replacement after a correction covers the same period, so it takes `-2`, and the store is asked which numbers are already spent.
- The number now belongs to `application/outgoing.py`, not to an adapter, so both modes and the fake produce the same one. `domain/invoice_numbers.py` holds the rules.

This supersedes the invoice-numbering line in `open-questions.md` and the counter in decision 11's manual mode. Invoices already sent keep the numbers they were sent under; the agent never renumbers anything.

## 30. In QuickBooks Online mode the bill rate comes off the consultant's product

Kevin has set QuickBooks Online up so that **each consultant is a product**, with their hourly rate on it, and has customised the invoice template so each line shows period ending date, description, hours, rate and amount.

Decision: **in QuickBooks Online mode the agent bills the rate on the product, not the rate in the engagement list.** One service item for everybody (`QBO_ITEM_NAME`) is gone; the agent looks the consultant's own product up by name and takes `UnitPrice` from it. Consequences:

- `Description` is the consultant's name, because the product is the consultant. `ServiceDate` is the **end of the billing period** -- that is the field Kevin's template labels "period ending".
- Customers and products are looked up by the name the engagement list spells, exactly. A client or consultant QuickBooks has never heard of is a `QUICKBOOKS_FAILED` naming them; the agent creates neither, and never bills a consultant under another consultant's product, which would bill the wrong rate.
- A product with no rate on it is refused rather than billed at zero.
- **The engagement list is still the cross-check.** The agent computes the invoice amount from its own bill rate, as it always did, and the existing "the total QuickBooks returns must equal the agent's to the cent" rule now catches the product's rate and the engagement list's having drifted apart: the invoice is voided and Kevin is told both figures. It has to work this way while both exist, because everything else the agent writes -- the preview, the approval question, the payment instruction, the tracking sheet, the guardrail on an unusual amount -- is built from the engagement list figure, and an invoice priced differently from all of them would make every one of those wrong.
- Manual mode is unchanged: there is no QuickBooks to ask, so the engagement list prices the invoice.

This amends rule 1 in `CLAUDE.md`, which said the bill rate comes only from the engagement list. The rule it was protecting is intact -- a rate never comes from an email, a timesheet, or the model -- but there are now two systems of record for the bill rate, and the agent refuses to invoice while they disagree.

The direction of travel is to stop keeping the rate in two places: once what the agent needs from the engagement list can be read from QuickBooks Online instead, the bill rate stops being a spreadsheet column and this cross-check goes with it. The **pay rate** cannot follow it there -- QuickBooks holds what Icon charges, not what Icon pays -- so the engagement list does not disappear on the strength of this.

Every QuickBooks call, and every way one can fail, is now logged as a JSON line (`logs.py`): the lookups and what they found, the create with its item id and number, a number QuickBooks assigned itself, a total that disagrees, a void, a retry, and the text of anything QuickBooks refused. Rates and amounts stay out of the log, as they do everywhere else.

## 31. The first real exercise may be Icon's own QuickBooks company, not a sandbox

`integrations/quickbooks-online.md` said sandbox first, and for development it still is: every test in this repository runs against recorded responses, and nothing about that changes.

For the **first live exercise**, though, Icon's own QuickBooks Online company is the better place, and Kevin has asked for it. The sandbox is full of Intuit's sample data, so nothing in it resembles what the agent will meet: the customers are not Icon's clients, the products are not Icon's consultants, and the invoice template is not the one Kevin has customised. A pass there would prove very little. Icon's own company has the real customers, the real per-consultant products with their rates, and the real template -- and Icon is not yet using QuickBooks Online for anything, so there is no live bookkeeping to disturb.

What makes this safe is `fops qbo-test-invoice` (the section above): it exercises the create path with no mailbox, no email and no approval reply, and deletes the invoice afterwards, so the company is left as it was found. Nothing about it can reach a client, because nothing about it sends anything.

Two things still hold, and are why this is a decision rather than a shortcut:

- **Before any run that is not this command**, each client's **Billing email** and **CC email** must be a stand-in address. The moment the mode is `ask_first` and Kevin replies "approve", the billing email goes wherever those two cells point. That is the roadmap's PR 13 box and it is not weakened by this.
- **Once Icon starts using QuickBooks Online for real**, this stops being true and the sandbox is the place again. This decision is about a window, not a policy.

## 32. A client is found in QuickBooks by its display name or its company name

`fops doctor` reported that QuickBooks had no customer called "Virginia Information Technology Agency", and QuickBooks was right: the customer's **display name** there is a person -- the contact the record was first created from -- and the organisation sits in the **company name** field. QuickBooks fills the display name from whatever was typed in first, so this is the ordinary shape of a customer record for an agency, not a mistake anyone made.

Decision: the name in the engagement list's "QuickBooks customer" column is matched against the **display name first, then the company name**. Neither field has to be changed in QuickBooks, and Kevin does not have to record a person's name in a column that says "client".

Display name is unique in QuickBooks; company name is not. So a company name matching **more than one** customer is refused, naming the candidates, rather than guessed between -- an invoice sent to the wrong customer record is not something the agent should be able to do by picking the first row. The way out of that is to put the display name of the one you mean in the "QuickBooks customer" column, which is what the column was always for.

This widens the lookup rule in decision 30 and does not otherwise change it: the agent still never creates a customer, and still refuses to invoice a client it cannot find.

## 33. The invoice is made before Kevin is asked, not after

Until now, ask first drew its own picture of the invoice -- a PDF rendered from the agent's template, numbered `(assigned on approval)` and attached as `proposed-invoice.pdf` -- and only made the real one once Kevin replied "approve". The reason was that QuickBooks has no draft invoices, so anything made before approval and then cancelled has to be voided rather than removed.

That reasoning has been overtaken. Since decision 30 the **rate comes off the consultant's product in QuickBooks**, and the PDF the client receives is rendered by **Kevin's own customised invoice template**, with period ending, description, hours, rate and amount laid out the way he arranged them. A PDF drawn here shares neither. Kevin would have been approving a picture of an invoice while a different-looking document went to the client, which is the opposite of what asking him is for.

Decision: **the invoice is created in the accounting system before the approval email is written**, and that email carries the real invoice PDF, under its real number. Nothing is sent to the client until Kevin answers.

Consequences:

- **Cancelling now voids.** Kevin replying "cancel" voids the invoice in QuickBooks and marks the agent's record cancelled. The voided invoice stays in the books and its number stays spent, which is what QuickBooks having no drafts costs; the replacement, if one comes, takes the next number along (decision 29). If the voiding itself fails, Kevin is told to void it by hand and the item is cancelled anyway, because he said so.
- **Making and sending are now separate steps** in `application/outgoing.py`: `prepare_invoice` makes and records it, `approve_item` writes the billing email for one that already exists. Both are idempotent per item, so asking and then approving makes one invoice, and a restart in between makes none.
- **Dry run is unchanged** and still creates nothing at all, in any accounting mode. It remains the stop button.
- A guardrail that sends an automatic item to ask first now also makes the invoice first. The guarantee that matters is untouched: nothing reaches a client without Kevin.

This reverses the "never before" rule in `integrations/quickbooks-online.md`, which came from decision 11 when the agent rendered its own invoices and QuickBooks Online was a plan rather than a thing Kevin had set up.

## 34. A cancelled invoice is renamed, so its number comes free

QuickBooks will not let a new invoice take a number that another invoice already holds, and a voided invoice still holds its own. So under decision 33, where the invoice exists before Kevin sees it, cancelling one would have pushed the replacement to `083126MT-PS-2` -- for the same consultant, the same client and the same month. The number is meant to say which month's work it is for, and a correction is not a different month.

Decision: cancelling an invoice **renames it to `083126MT-PS-VOID` and then voids it**, in that order. The real number is free again, and the replacement is `083126MT-PS`.

- **The rename happens first**, while the invoice is still an ordinary one. A voided invoice is not something to count on being editable, and the number cannot come free until something else holds it.
- **A rename that fails does not stop the void.** An invoice Kevin cancelled must not survive because its number could not be changed; the number stays spent and the replacement takes the next one along, which is untidy rather than wrong.
- **A second void of the same number** becomes `-VOID2`, and so on; the agent asks its own records which names are taken.
- **The name is renamed in the agent's records too**, so manual mode behaves the same way and the `-2` logic sees the number as free.
- **`find_invoice` ignores a voided number.** After a crash the agent asks QuickBooks whether it already made this item's invoice, matching on the item id in the private note -- which a cancelled invoice still carries. One the agent cancelled is not an answer to that question, and without this the replacement would have been the voided invoice.

`-VOID` is the agent's own marker rather than anything QuickBooks defines, which is why `is_voided_number` in `domain/invoice_numbers.py` is the single place that decides what one looks like.

## 35. A mail folder says what the message needs, and is revisited when that changes

The agent files each message it reads into `Agent/Processed`, `Agent/Needs Review` or `Agent/Ignored`. The folder is chosen while the message is being **read**, which is long before the invoice is made: a timesheet that reads cleanly is filed as processed, and then the invoice is created, and only then can QuickBooks refuse it. The email that started it all sits in the processed folder saying nothing is wrong.

Decision: when a review is raised about an item **after** its timesheet was read, the emails that timesheet arrived on are **moved to `Needs Review`**. The folder keeps one meaning -- something about this needs a person -- rather than meaning "the reading went fine" in one place and "the invoice went fine" in another.

This needed `inbox.move` to look beyond the inbox: it searched `INBOX` only, so moving an already-filed message quietly did nothing. It now tries the inbox first, then the agent's own folders, and does nothing if the message is already where it is being sent.

**What was considered and rejected: a folder per item state**, such as `Waiting for approval`, holding the timesheet until its invoice is approved and sent.

- One email can feed several items. A consultant sends a timesheet and their firm's invoice together (decision 24), and an email can carry timesheets for more than one period. If one item is approved and another is still waiting, there is no folder the message belongs in. Mail folders cannot hold per-item state because the relationship is not one to one.
- Every move is a chance to lose a message, and mirroring state means moving on every transition and back again on a correction. Nothing in the never-twice guarantees depends on where a message sits, and this would have made something depend on it.
- The agent's mailbox is not Kevin's. He reads his own, where the approval email with the real invoice attached is already waiting. A folder in the agent's mailbox serves whoever is debugging.
- "What is waiting on approval" is already answered, by `fops status`, the tracking sheet, and the Monday summary.

The folder remains a courtesy for a person looking at the mailbox. The database is the record.

## 36. A product is an engagement, not a person

Decision 30 made each consultant a product in QuickBooks, with their rate on it. That only works while a consultant has one rate. Icon's engagement list is explicitly one row per consultant **per client**, so a consultant working at two clients has two bill rates, and one product cannot hold both: the agent would have billed one of them at the other's rate, and its own total check would have voided the invoice with "the product's rate and the engagement list have stopped agreeing". None of Icon's current engagements repeats a consultant, so it had not bitten.

Decision: **a product is an engagement.** It sits under a QuickBooks **category named for the client**, so its `FullyQualifiedName` is `MasTec:Sridhar Doraiswamy`, and that is what the agent looks it up by.

Why the category rather than the name or the SKU:

- QuickBooks enforces uniqueness on an item's name, so two products could not both be called `Sridhar Doraiswamy`. Under different categories they can, because the qualified path is the identity.
- `FullyQualifiedName` is **read-only, system-defined, filterable and sortable**. QuickBooks maintains it, so it cannot drift out of step with the hierarchy the way a hand-typed key does, and the lookup stays one exact query.
- `Sku` was the other candidate. It is not returned at all without a `minorversion` parameter, which the agent does not send, its uniqueness is not enforced, and it would still not have allowed two products of the same name.
- In the QuickBooks interface the products end up grouped by client, which is how Kevin reads them anyway.

The client is looked up under the names it might be filed under, in turn: the engagement list's short name, then the "QuickBooks customer" name, then the legal name. Each is an exact match, the first that finds a product wins, and the log says which matched -- the same shape as the customer lookup in decision 32.

**A product not yet under a category is still used**, but only while exactly one product answers to that name: a bridge for filling the categories in. Two products sharing a name with no category to tell them apart is refused and both are named, because that is precisely the case that would bill the wrong rate.

`fops doctor` checks **per engagement** now, not per consultant, for the same reason.

## 37. The purchase side is read before it is trusted

QuickBooks holds both sides of an engagement's product: `UnitPrice`, what the client is billed, and `PurchaseCost`, what Icon pays for those hours, with a preferred vendor saying who it pays. The bill rate moved to QuickBooks in decision 30. The pay rate is the obvious next thing to move, and moving it blind would be a poor trade: the pay rate feeds the payment instruction Kevin acts on, and unlike the bill rate nothing about it is checked against an invoice afterwards.

Decision: the agent **reads** the purchase side and **does not use it yet**. `fops doctor` gains a **quickbooks pay rates** check that compares what QuickBooks holds with what the engagement list says, so the two can be made to agree before anything depends on either.

- A product with nothing on its purchase side is **not a disagreement**; it has not been filled in, and the check says how many are in that state rather than failing.
- A purchase cost or a preferred vendor that **differs** is a failure, naming both figures. Nothing is paid from QuickBooks today, so no payment instruction is wrong because of it -- but they have to agree before the pay rate moves, and a difference found now is a difference nobody has to debug later.
- The **bill rate** is now compared too, in the products check, and a difference there is a failure for a harder reason: the invoice would be created, found to disagree, and voided (decision 30). That was only discoverable by watching an invoice fail.

`fops doctor` truncated a failing check's detail at 300 characters, which was fine while every failure was one line. These checks name one line per engagement, so the limit is now generous enough to show them all and says when it has cut something short: a check that silently drops the entries someone needs is worse than one that says nothing.

The step this sets up is moving the pay rate and the payee across, which needs more than a lookup: the amount owed is worked out when a timesheet is read and stored on the item, and that happens in the application layer from the engagement list alone, with no accounting system in reach. That is a change of shape, not a change of source, and it waits until the two agree.

## 38. What Icon pays comes from the product's purchase side

Decision 37 read the purchase side without using it, so the two sources could be compared first. `fops doctor` now reports they agree for every one of Icon's engagements, so the move can be made.

Decision: **the pay rate and the payee come from the engagement's product in QuickBooks** -- `PurchaseCost` and the preferred vendor -- in the same way the bill rate has since decision 30. The engagement list stays the cross-check.

- **Where QuickBooks has nothing on the purchase side, the engagement list is used.** An empty purchase side is one that has not been filled in, not a statement that nothing is owed. This is what lets the categories and costs be filled in at Kevin's pace.
- **A disagreement uses QuickBooks' figure and tells Kevin**, because that is where the rate lives now and he is the one who pays. The review **pauses the item** as every review does, which means the client's invoice waits on a disagreement that has nothing to do with it. That is the price of one mechanism rather than two, and `fops doctor` is what keeps it rare: it compares the two before any timesheet arrives, so a difference is found while someone is looking at the engagement list rather than when an invoice is due.
- **An accounting system that cannot answer does not stop the run.** The engagement list still has a rate, the timesheet is still read, and the invoice happens on a later run anyway. A QuickBooks outage must not stop the agent reading mail.

This is a change of shape, not only of source: the amount owed is worked out when a timesheet is read, so the accounting system is now consulted while an item is being created, in `application/run.py`. That is the first time the accounting port is asked anything outside invoicing, which is why `AccountingSystem` gained `engagement_rates` rather than the application reaching for an adapter.

What still comes from the engagement list about paying: **how** Icon pays (bank transfer, payroll, check) and **when** (the pay timing days). Both have plausible homes on a QuickBooks vendor record, and neither is worth moving until the rest of the residue moves with it.

## 39. An item belongs to an engagement, not to a pair of names

An engagement is a product in QuickBooks (decision 36), and its client and consultant come from that product's category path. Names get tidied: `MasTec` becomes `MasTec Inc`, a consultant's spelling is corrected. Items were found by consultant and client name alone, so a rename would have orphaned every item in flight -- the agent would have stopped finding them, expected fresh invoices for work already in hand, and left the originals waiting for ever.

Decision: **an item carries the accounting system's id for its engagement, and is found by that first.** The consultant and client names stay on the item as the label a person reads, and **catch up** when the accounting system's names change.

- Found by id under different names, the item is **relabelled**, not duplicated. It is the same engagement; the id says so.
- Found by neither, it is a new item, as before.
- **Both places that look for an item do this**: the one that reads a timesheet, and the one that works out which invoices to expect. The second runs first in a run, so leaving it looking by name alone would have made the duplicate before the rename could be noticed.
- **An empty id matches nothing.** Manual mode has no accounting system to have an id in, and those items are still found by name; an empty id must not match all of them.

`items.engagement_ref` is nullable for items made before this and for manual mode. The unique constraint still stands on consultant, client and period -- one invoice per consultant per client per month (CLAUDE.md rule 3) is unchanged, and the id is the identity rather than a second key.
