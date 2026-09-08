# Emails the agent sends

The agent works by email. Kevin never has to open a program: everything he needs arrives in his inbox, and he answers by replying. This file lists every email, who gets it, and roughly what it says. The wording is a starting point for Kevin to adjust.

All emails go out from the agent's own mailbox (for example `jay@icon-technologies.com`). Replies come back to that mailbox, which the agent reads. Emails to clients have Reply-To set to Kevin so client questions reach him directly as well.

## To Kevin

### 1. Timesheet details for your records

Sent every time a timesheet is read, before any checking is finished (Objective 2).

- **Subject:** `Timesheet received: Priya Shah — Acme Corp — Aug 1–31, 2026 — 156.00 hours`
- **Body:** consultant, client (and end client), period, total hours, daily hours if shown, approval (what and by whom), what the agent will do next (invoice, or which review reasons it found), and the engagement row it matched.
- **Attachments:** the original timesheet file.

### 2. Needs your review

Sent when one or more checks fail (see `timesheet-checks.md`). One email per timesheet, listing every problem found.

- **Subject:** `Needs your review: Priya Shah — Acme Corp — Aug 2026 — no approval found`
- **Body:** what the agent found (same summary as above), each problem in plain words, and what Kevin can do: fix the engagement list, or reply with the answer, or reply "ignore". Examples of replies that work are included.
- **Attachments:** the original timesheet file.

Kevin's reply is read by the agent. Only replies from `kevin@icon-technologies.com` in the same thread count. The agent re-runs the checks with the answer; if it still cannot proceed, it replies again saying what is still missing.

### 3. Approve this invoice?

Ask first mode only. Sent when a timesheet passes every check.

- **Subject:** `Approve? Invoice for Acme Corp — Priya Shah — Aug 2026 — $21,840.00`
- **Body:** client, the billing contact it will go to, consultant, period, hours, bill rate, total, due date, and the billing email exactly as it will be sent.
- **Attachments:** the invoice PDF and the timesheet.
- **Kevin replies** `approve` to send, or `cancel` (with a reason if he likes) to stop. Anything else gets a short reply asking for one of the two words. Only replies from Kevin's address in the same thread count.

In automatic mode this email is not sent for engagements marked "send automatically"; Kevin sees the billing email itself instead, because he is always on CC.

### 4. Payment instruction

Sent when the invoice has been sent (or, in dry run mode, when the item is ready), so Kevin can pay the consultant or vendor as he does today (Objective 5).

- **Subject:** `Payment due 2026-09-15: Priya Shah — Aug 2026 — $15,600.00`
- **Body:** who to pay (the consultant, or the vendor company and its contact), period, approved hours, pay rate, amount owed, due date, how they are paid (bank transfer / payroll / check), and the invoice this relates to.
- **Attachments:** none.

The agent does not follow up on this email and does not track whether the payment was made.

### 5. Monday summary

Every Monday morning.

- **Subject:** `Weekly summary — week of Sep 7, 2026`
- **Body:** timesheets received last week (one line each); invoices sent (one line each, with amounts); items waiting for Kevin's review, with the reason; items waiting for approval; engagements with no timesheet yet for the last period; unpaid invoices past their due date, when the agent knows about them; emails set aside from unknown senders; duplicates filed.
- **Attachments:** the tracking sheet (`tracking.xlsx`).

### 6. Something needs attention

Sent at most once a day per problem: the mailbox connection stopped working, QuickBooks Online needs to be reconnected (with the steps to do it), or the engagement list could not be opened.

## To the client

### 7. Billing email

One per invoice (Objective 4).

- **To:** the client's billing email(s) from the engagement list.
- **CC:** Kevin, always, plus any CC email on the client's row.
- **Reply-To:** Kevin.
- **Subject:** `Icon Technologies invoice 1043 — Priya Shah — August 2026`
- **Body (starting point):**

  > Hello,
  >
  > Please find attached Icon Technologies invoice 1043 for Priya Shah's services for August 1–31, 2026 (156.00 approved hours), along with the approved timesheet. Payment terms are 30 days; the invoice is due October 3, 2026.
  >
  > Please let us know if you have any questions.
  >
  > Thank you,
  > Icon Technologies

- **Attachments:** the invoice PDF, and the consultant's original timesheet file exactly as it was received.
- If the client's row says `portal`, the agent does not send this email. It emails Kevin the invoice and timesheet instead, and Kevin uploads them.
- When an invoice replaces an earlier one (after a corrected timesheet), the first line says so: "This invoice replaces invoice 1043, which has been cancelled."

The billing email never mentions the pay rate. The payment instruction never mentions the bill rate. The consultant is never on the billing email.

## Before anything goes to a client

The agent checks, and records that it checked, that: the billing email address is the one on the client's row; the consultant, client, and period on the invoice match the timesheet item; the total is hours × the bill rate from the engagement list; the invoice PDF and the original timesheet file are attached; Kevin is on CC; and no invoice has already been sent for this consultant and period. It writes down that it is about to send, then sends, then writes down that it did. If the agent restarts in between, it looks for the email it was about to send before trying again, so nothing goes out twice.

## Client replies

Replies from clients land in the agent's mailbox. The agent does not answer them. It forwards them to Kevin unchanged and lists them in the Monday summary. Emails in the mailbox that are not timesheets, replies from Kevin, or client replies are left in a *Needs Review* folder for Kevin to look at.
