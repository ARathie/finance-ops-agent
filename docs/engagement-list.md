# The engagement list

The engagement list is the spreadsheet Kevin keeps with every client, consultant, vendor company, and engagement. The agent reads it at the start of every run. It is the **only** place the agent takes rates, billing contacts, and payment terms from. Nothing in an email or on a timesheet can override it.

File: `engagements.xlsx` (location set in the agent's settings; a CSV export of each sheet works too). Kevin edits it in Excel like any other spreadsheet. The agent never writes to it.

## Sheet: Clients

One row per company Icon sends invoices to. If Icon invoices a staffing company rather than the place the consultant works, the staffing company is the client here.

| Column | Meaning | Example |
|---|---|---|
| Client | Short name used everywhere else in the workbook | Acme Corp |
| Legal name | Name to print on the invoice | Acme Corporation |
| Billing contact | Person or team who receives invoices | Accounts Payable |
| Billing email | Where the invoice goes; separate several with `;` | ap@acme.example |
| CC email | Anyone else at the client to copy; optional | jane.doe@acme.example |
| Payment terms (days) | Days the client has to pay after the invoice date | 30 |
| Delivery | `email` (the agent sends it) or `portal` (Kevin uploads it; the agent only prepares it) | email |
| Time system | What the consultant's timesheets come from, if known; helps the agent recognise the format | Fieldglass |
| Names on timesheets | Other names this client appears under on timesheets; separate with `;` | Acme; ACME Corp. |
| Email domains | Email domains that count as this client when a manager forwards an approval; separate with `;` | acme.example |
| QuickBooks customer | The customer name exactly as it appears in QuickBooks | Acme Corporation |
| Notes | PO numbers, special instructions | PO 4471 must appear on invoice |
| Active | `yes` or `no` | yes |

## Sheet: Consultants

One row per person doing work.

| Column | Meaning | Example |
|---|---|---|
| Consultant | Full name | Priya Shah |
| Other names | Names the person appears under on timesheets; separate with `;` | P. Shah; Shah, Priya |
| Email | Addresses this person sends timesheets from; separate with `;` | priya@example.com; pshah@acme.example |
| Type | `employee` (Icon W-2), `contractor` (independent), or `vendor` (through another company) | contractor |
| Vendor company | Required when Type is `vendor`; must match a row on the Vendors sheet | |
| Paid by | `bank transfer`, `payroll`, `check`, or `other` | bank transfer |
| Pay timing (days) | Days after the end of the billing period the payment is due | 15 |
| Active | `yes` or `no` | yes |

## Sheet: Vendors

One row per company that supplies consultants to Icon. Only needed when a consultant's Type is `vendor`.

| Column | Meaning | Example |
|---|---|---|
| Vendor company | Name | Blue Peak Consulting LLC |
| Contact email | Where questions about payment go; separate with `;` | billing@bluepeak.example |
| Paid by | `bank transfer`, `check`, or `other` | bank transfer |
| Pay timing (days) | Days after the end of the billing period the payment is due | 30 |
| Active | `yes` or `no` | yes |

## Sheet: Engagements

One row per consultant working for one client. When a rate changes, add a new row with the new rates and the date they start; leave the old row as it is.

| Column | Meaning | Example |
|---|---|---|
| Consultant | Must match a row on the Consultants sheet | Priya Shah |
| Client | Must match a row on the Clients sheet | Acme Corp |
| End client | Where the consultant actually works, if not the client; optional | Northwind Bank |
| Role | Printed on the invoice line | Senior PeopleSoft Developer |
| Start date | First day of the engagement | 2026-02-01 |
| End date | Last day, or blank while ongoing | |
| Billing schedule | `monthly`, `twice a month`, `every two weeks`, or `weekly` | monthly |
| First period start | For `every two weeks` and `weekly` only: the first day of any one period, so the agent can work out the rest | 2026-02-02 |
| Bill rate | Dollars per hour Icon charges the client | 140.00 |
| Pay rate | Dollars per hour Icon pays the consultant or vendor | 100.00 |
| Rates from | The date these rates apply from; use the start date for the first row | 2026-02-01 |
| Send automatically | `yes` lets the agent send clean invoices without asking when it is in automatic mode; `no` always asks | no |
| Active | `yes` or `no` | yes |

## Rules the agent follows

- Rates, billing contacts, payment terms, and pay timing come only from this workbook.
- For a timesheet, the agent uses the Engagements row for that consultant and client whose "Rates from" date is the latest one on or before the first day of the billing period. If the rate changes in the middle of a period, the agent asks Kevin rather than splitting the invoice.
- There must be exactly one active engagement for a consultant and client on any given date. Two rows with the same consultant, client, and "Rates from" date are an error.
- Billing periods: `monthly` = calendar month; `twice a month` = 1st to 15th and 16th to month end; `every two weeks` = 14-day periods counted from "First period start"; `weekly` = 7-day periods counted from "First period start". The first and last period of an engagement are cut short at the start and end dates.
- The agent never edits this file. If a row is incomplete or contradictory (missing rate, unknown client name, `vendor` type without a vendor company, no billing email for a client delivered by email, a pay rate higher than the bill rate), the agent emails Kevin a review item naming the sheet and row, and leaves any affected timesheets waiting.
- Money in this sheet is entered in dollars with cents (140.00). The agent works in whole cents internally so totals never drift.

## Example

Clients: Acme Corp (ap@acme.example, 30 days, email). Consultants: Priya Shah (contractor, bank transfer, 15 days). Engagements: Priya Shah at Acme Corp, monthly, bill 140.00, pay 100.00, rates from 2026-02-01.

Priya emails her approved August timesheet showing 156 hours. The agent invoices Acme Corp $21,840.00 (156 × 140.00), due 30 days after the invoice date, and tells Kevin Priya is owed $15,600.00 (156 × 100.00), due 15 days after August 31.
