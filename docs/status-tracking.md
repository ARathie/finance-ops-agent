# Statuses and the tracking sheet

## One record per consultant per billing period

The agent keeps one record, called a timesheet item, for each consultant and billing period, for example "Priya Shah — Acme Corp — August 2026". Everything about that month's timesheet, invoice, and payment instruction hangs off that one record, and there can never be two of them for the same consultant and period. That is how the agent avoids sending two invoices for the same work.

## Statuses

| Status | Meaning |
|---|---|
| `waiting_for_timesheet` | The billing period has ended for an active engagement and no timesheet has arrived yet. Listed in the Monday summary. |
| `received` | A timesheet has arrived and is being read and checked. If the engagement is billed monthly and timesheets come weekly, the item stays here until the whole period is covered. |
| `needs_review` | Something is unclear; Kevin has been emailed. The item waits here until Kevin answers or fixes the engagement list. |
| `ready` | Everything checks out. The invoice and billing email are prepared. In dry run mode the item stops here and Kevin does the rest by hand. |
| `waiting_for_approval` | Ask first mode: Kevin has been emailed "Approve this invoice?" and has not replied yet. |
| `invoice_sent` | The invoice exists (in QuickBooks Online, or as a PDF for Kevin to enter) and the billing email went to the client with Kevin on CC. The payment instruction has been emailed to Kevin. |
| `client_paid` | QuickBooks Online shows the invoice as paid (checked daily once connected), or Kevin told the agent it was paid. |
| `ignored` | Not a real timesheet item: a duplicate, something Kevin said to ignore, or an email that was not a timesheet after all. |
| `cancelled` | Kevin cancelled it (for example the consultant was on leave and there is nothing to bill). |

Each item also records, with dates: the email the timesheet came in, the details-for-records email to Kevin, the review emails and Kevin's answers, the invoice number, the QuickBooks invoice id, the billing email, and the payment instruction email.

## Allowed status changes

An item starts as `waiting_for_timesheet` (the period ended and nothing has arrived) or `received` (a timesheet arrived first). From there, only these changes are allowed; the software refuses anything else.

| From | Can change to | When |
|---|---|---|
| `waiting_for_timesheet` | `received` | The timesheet arrives. |
| | `cancelled` | Kevin cancels it (for example the consultant was on leave). |
| `received` | `ready` | Every check passes. |
| | `needs_review` | A check fails; Kevin is emailed. |
| | `ignored` | It was a duplicate, or not a timesheet after all. |
| | `cancelled` | Kevin cancels it. |
| `needs_review` | `ready` | Kevin's answer (or a fixed engagement list) clears everything. |
| | `received` | Kevin's answer sends it back to checking or to waiting for more weekly timesheets. |
| | `ignored` | Kevin replies "ignore". |
| | `cancelled` | Kevin cancels it. |
| `ready` | `waiting_for_approval` | Ask first mode: Kevin is emailed "Approve this invoice?". |
| | `invoice_sent` | Automatic mode sends it directly. (In dry run the item stays `ready`.) |
| | `needs_review` | Sending or creating the invoice failed, or a corrected timesheet arrived. |
| | `cancelled` | Kevin cancels it. |
| `waiting_for_approval` | `invoice_sent` | Kevin replies "approve". |
| | `needs_review` | Sending failed after Kevin approved, or a corrected timesheet arrived. |
| | `cancelled` | Kevin replies "cancel". |
| `invoice_sent` | `client_paid` | QuickBooks Online shows it paid, or Kevin says so. |
| | `needs_review` | A corrected timesheet arrived after the invoice went out. |
| `client_paid`, `ignored`, `cancelled` | — | Final. Nothing changes these. |

A corrected timesheet accepted with "use the new one" travels back through this table: the item returns to `received` and is checked again from the start (cancelling and replacing the old invoice first if one was already sent).

The agent does not track whether the consultant or vendor was paid. That stays with Kevin, as today.

## Duplicates

Consultants resend, forward, and attach the same timesheet to several emails. The agent handles this quietly:

- the same email arriving again (for example after a restart) is skipped;
- the same file arriving in a new email is filed against the existing item and noted in the Monday summary;
- a new file with exactly the same dates, hours, and approval for an item already handled is treated the same way.

No second invoice is ever created for these.

## Corrected timesheets

Sometimes a consultant sends a fixed version: different hours, or a different approval. The agent notices that it already has a timesheet for that consultant and period, compares the two, and emails Kevin a `CORRECTION` review showing both versions and what changed. Kevin replies:

- **"use the new one"** — if the invoice has not been sent, the agent starts the item over with the new timesheet. If the invoice has already been sent, the agent cancels the old invoice in QuickBooks Online (or asks Kevin to, until QuickBooks is connected), creates a new one, and sends a new billing email saying it replaces the earlier invoice number. A new payment instruction goes to Kevin with the corrected amount.
- **"ignore"** — the new file is filed and nothing changes.

## The tracking sheet

For Kevin's records the agent keeps an Excel file, `tracking.xlsx`, with one row per timesheet item, rewritten after every run. This mirrors the spreadsheet records Kevin keeps today, so nothing has to be typed twice.

| Column | Example |
|---|---|
| Consultant | Priya Shah |
| Client | Acme Corp |
| Period | 2026-08-01 to 2026-08-31 |
| Approved hours | 156.00 |
| Bill rate | 140.00 |
| Invoice amount | 21,840.00 |
| Pay rate | 100.00 |
| Amount owed | 15,600.00 |
| Owed to | Priya Shah (bank transfer), due 2026-09-15 |
| Status | invoice_sent |
| Timesheet received | 2026-09-02 |
| Invoice number | 1043 |
| Invoice sent | 2026-09-03 |
| Client paid | |
| Notes | corrected timesheet received 2026-09-05, used new one |

The sheet is a copy for reading and filing; the agent's own database is what it works from. Kevin can open, sort, and copy from it freely, and it is safe to delete because the agent rewrites it.

## Missing timesheets

When a billing period ends for an active engagement and nothing has arrived after 7 days (adjustable), the item appears in the Monday summary under "no timesheet yet". The agent does not chase consultants itself.
