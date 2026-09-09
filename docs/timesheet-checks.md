# Reading and checking a timesheet

What the agent pulls out of a timesheet, how it decides which engagement it belongs to, the checks it runs, and every reason it can ask Kevin for a review.

## What the agent reads from a timesheet

Timesheets arrive in many layouts: a PDF from the client's time system, a spreadsheet, a screenshot, a one-page summary, a day-by-day report, or a forwarded email chain. The agent uses an AI model (Claude) to read the attachment and fill in the same short form every time:

| Field | What it is |
|---|---|
| Consultant name | As written on the timesheet |
| Client name | The company named on the timesheet, if any |
| End client name | If a different company is named as where the work was done |
| Period start, period end | First and last date covered |
| Daily hours | Date and hours for each day, when the timesheet shows them |
| Total hours | The total printed on the timesheet |
| Approval | What shows the client approved it: an `Approved` status, an approver's name and date, a signature, or a forwarded approval email from the client. Or nothing. |
| Anything odd | Overtime lines, expenses, more than one rate, more than one person, notes |

For every field the model also records the exact words on the page it relied on and how sure it is (`high`, `medium`, or `low`). The model only reads. It does not add up hours, does not know any rates, does not decide which engagement this is, and does not decide who gets emailed. All of that is done by ordinary code using the engagement list, so it is predictable and testable.

If the email has no attachment, or the attachment cannot be opened, the agent asks Kevin rather than trying to read hours out of the email text.

## Deciding whose timesheet it is

1. **Consultant.** If the sender's address is on a consultant's row in the engagement list, that is the consultant. Otherwise the name on the timesheet is compared with the "Consultant" and "Other names" columns (ignoring case, punctuation, and the order of first and last name). No match, or two matches, means a review.
2. **Client and engagement.** The agent takes the consultant's active engagements that cover the timesheet's dates. If there is exactly one, that is it. If there are several, the client name on the timesheet (compared with "Client", "Legal name", "Names on timesheets", and "End client") decides. Still unclear means a review.
3. **Billing period.** The timesheet's dates must fit inside one billing period on that engagement's schedule (see `engagement-list.md`). A timesheet for part of a period is kept while the agent waits for the rest; a monthly engagement with weekly timesheets is invoiced once the whole month is covered. A timesheet that runs across two periods is a review.
4. **Rates.** From the engagement row in force on the first day of the period.
5. **Hours.** If the timesheet shows daily hours, they must add up to the printed total (to the quarter hour). The total is what gets invoiced.

## The checks, in order

Each check either passes or creates a review item with one of the reasons below. The agent finishes all the checks it can before emailing Kevin, so one email lists everything wrong with a timesheet.

1. Is the sender someone we know?
2. Is there an attachment we can read?
3. Have we seen this exact file before? (If so, it is a duplicate and is filed quietly.)
4. Can we tell which consultant it is?
5. Can we tell which engagement (client) it is, and is it active for these dates?
6. Can we tell which dates it covers, and do they fit the billing schedule?
7. Can we find the hours, and do the daily hours add up to the total?
8. Are the hours believable? (More than 24 in a day, more than 25% over full time for the period — full time counted as 8 hours per weekday — or zero.)
9. Was it approved, and can we see by whom or how?
10. Is the model sure enough about the consultant, dates, hours, and approval? ("Sure enough" means none of those read with low confidence.)
11. Is there a rate for these dates, and a billing email for the client? (A rate that changes in the middle of the period is a `LIST_ROW_PROBLEM` review naming both rows — the agent never splits an invoice.)
12. Have we already handled a different timesheet for this consultant and period? (If so, this may be a correction.)

## Review reasons

The code is what the software uses; the message is what Kevin sees. Every review email also says what Kevin can do: fix the engagement list, reply with the answer, or reply "ignore".

| Code | Message Kevin sees | What usually fixes it |
|---|---|---|
| `UNKNOWN_SENDER` | This came from an address I don't recognise. | Add the address to the consultant's row, or reply "ignore". |
| `NO_ATTACHMENT` | This looks like a timesheet email but has no attachment I can use. | Ask the consultant to resend, or reply with the hours and dates. |
| `CANT_READ_ATTACHMENT` | I couldn't read the attachment. | Ask for a PDF or spreadsheet, or reply with the details. |
| `CONSULTANT_UNKNOWN` | I can't tell which consultant this timesheet is for. | Add the name to "Other names", or reply with the consultant's name. |
| `ENGAGEMENT_UNCLEAR` | I can't tell which client this is for, or there is no active engagement for these dates. | Fix the engagement row, or reply with the client. |
| `PERIOD_UNCLEAR` | I can't tell which dates this covers. | Reply with the first and last date. |
| `PERIOD_MISMATCH` | The dates don't line up with the billing schedule for this engagement. | Fix the schedule on the engagement row, or reply with the period to use. |
| `HOURS_MISSING` | I can't find the hours on this timesheet. | Reply with the approved hours. |
| `HOURS_DONT_ADD_UP` | The daily hours don't add up to the total. | Reply with the hours to use. |
| `HOURS_UNUSUAL` | The hours look unusually high, or are zero. | Reply "hours are right" or with the correct hours. |
| `NO_APPROVAL` | I can't see that the client approved these hours. | Forward the client's approval, or reply "approved by <name> on <date>". |
| `NOT_SURE` | I read this timesheet but I'm not confident about <field>. | Confirm or correct the field. |
| `RATE_MISSING` | The engagement list has no rate for these dates. | Add a row with the rate and its start date. |
| `NO_BILLING_CONTACT` | The engagement list has no billing email for this client. | Add it to the Clients sheet. |
| `LIST_ROW_PROBLEM` | A row in the engagement list is incomplete or contradicts another row. | Fix the row named in the email. |
| `CORRECTION` | This looks like a corrected version of a timesheet I already handled. | Reply "use the new one" or "ignore". See `status-tracking.md`. |
| `SEND_FAILED` | I couldn't send the billing email. I'll keep trying; please check the mailbox. | Usually fixes itself; otherwise check the mailbox connection. |
| `SEND_UNCERTAIN` | I sent the billing email for <item> but couldn't confirm it left the server. You're on CC: reply "received" if you got it, or "resend". | Reply "received" or "resend". |
| `QUICKBOOKS_FAILED` | I couldn't create the invoice in QuickBooks. I'll keep trying; please check the connection. | Reconnect QuickBooks (steps are in the email). |
| `MAILBOX_PROBLEM` | I can't read the mailbox. | Follow the steps in the email. |
| `QUICKBOOKS_RECONNECT` | QuickBooks needs to be reconnected. | Follow the steps in the email. |

Duplicates are not review items: a second copy of the same file, or a timesheet with exactly the same hours and dates for the same consultant and period, is filed with a note in the Monday summary and nothing else happens.

## How Kevin answers

Kevin replies to the review email. The agent only accepts answers that come from Kevin's address as a reply in the same email thread. Short answers work: "this is Acme", "use 152 hours", "approved by Jane Doe on 9/3", "ignore", "use the new one". The agent re-runs the checks with the answer and either carries on or asks again. Kevin can also just fix the engagement list; the agent notices at its next run and re-checks anything waiting.
