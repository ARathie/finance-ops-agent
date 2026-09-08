# Project Scope and Objectives

## Project Goal

Build an AI-enabled agent for Icon Technologies that reduces the manual work involved in receiving consultant timesheets, extracting and validating billing information, creating client invoices, sending those invoices, and calculating consultant pay.

The project is intended to be a **real, usable business system**, not a toy demo.

It should create measurable operational value for Icon while also demonstrating the ability to build a production-minded AI workflow with evaluation and reliability considerations.

## Important Scope Clarification

The agent does **not** interact with candidates.

The project does **not** include:
- candidate sourcing,
- candidate outreach,
- resume screening,
- candidate qualification calls,
- interview scheduling,
- or candidate assessment.

The agent's email capability is for **billing and timesheet operations**.

---

# Objective 1: Create AI Agent with Email Capability

The agent should:

1. Continuously monitor its own business email inbox for consultant timesheets and billing-related messages.
2. Identify relevant emails and attachments.
3. Determine which consultant, client, and billing period the message concerns.
4. Track processed timesheets so the same timesheet is not processed twice.
5. Send client invoice emails from its own email account.
6. Maintain enough context to understand the status of each billing workflow.

A representative agent email address could be something like:

`jay@icon-technologies.com`

The exact address is not important to the business logic.

---

# Objective 2: Ingest Consultant Timesheet and Extract Data

For an incoming timesheet, the system should identify and extract the information required for billing.

Expected information includes:
- consultant name,
- client,
- billing period,
- approved hours,
- approval status,
- and any other information necessary to associate the timesheet with the correct engagement.

The system should recognize that timesheets may have different formats.

It should identify and flag issues such as:
- no evidence of approval,
- missing hours,
- unclear consultant identity,
- unclear billing period,
- inconsistent totals,
- duplicate submissions,
- unreadable attachments,
- or data that conflicts with known engagement information.

The system should match the timesheet to the correct consultant/client relationship before an invoice is produced.

After extraction, the system should email the administrator (kevin@icon-technologies.com) the extracted timesheet details for his record keeping.

---

# Objective 3: Create Client Invoice

Using the validated timesheet information and the approved client contract information, the system should prepare the correct client invoice.

The business calculation is:

`Invoice amount = approved hours × client bill rate`

The client bill rate must come from a trusted business record associated with that consultant/client engagement.

The system should produce an invoice containing the correct:
- client,
- consultant or service description,
- billing period,
- approved hours,
- client bill rate,
- total amount,
- and relevant payment terms.

The current desired accounting destination is **QuickBooks Online**, if the business migrates from QuickBooks Desktop.

The business requirement is more important than the product choice:
the invoice must be correctly created and incorporated into Icon's accounting process.

---

# Objective 4: Email Client Invoice to Client

Once an invoice is ready, the agent should prepare and send the billing email.

The outgoing billing package should contain:
- the client invoice,
- the approved consultant timesheet,
- and an appropriate billing email.

Before sending, the workflow must ensure that:
- the recipient is correct and the administrator (kevin@icon-technologies.com) is CC'd,
- the consultant is correct,
- the billing period is correct,
- the amount is correct,
- and the correct attachments are included.

The system should also retain the status of the invoice, including whether it has been sent and whether payment has been received when that information is available.

---

# Objective 5: Calculate Contractor Pay and Generate Payment Instructions

Using the same approved hours, the system should calculate what Icon owes the consultant or subcontracting company.

The business calculation is:

`Contractor pay = approved hours × contractor pay rate`

The contractor pay rate must come from a trusted engagement record.

The system should generate a clear payment instruction containing:
- consultant or vendor,
- billing period,
- approved hours,
- pay rate,
- amount owed,
- due date or expected payment timing,
- and relevant payment details/status.

The first version does not need to automatically release funds.

A human can review and execute payment.

---

# Desired Outcome

For a normal timesheet with no exceptions, the ideal business experience is:

1. Consultant emails approved timesheet.
2. Agent recognizes and processes it.
3. Agent extracts the required billing data.
4. Agent validates the timesheet.
5. Agent matches the consultant to the correct client and rates.
6. Client invoice is prepared.
7. Billing email is prepared and sent.
8. Contractor pay is calculated.
9. Billing and payment status are recorded.
10. A human only needs to intervene when approval or an exception is required.

The purpose is to turn a repetitive manual monthly workflow into an exception-driven workflow.
