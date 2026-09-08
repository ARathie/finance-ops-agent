# Business Data and Entities

This file describes the important business concepts the workflow operates on.

It is not a technical data-model specification.

## Consultant

A person performing work for a client.

Relevant information may include:
- full name,
- email,
- consultant/vendor company,
- worker type,
- active engagements,
- payment terms,
- payment status.

A consultant may be paid directly by Icon or through another consulting company.

## Vendor / Subcontracting Company

A company that supplies a consultant to Icon.

In these cases:
- Icon invoices its client,
- the subcontracting company invoices or is paid by Icon,
- and the subcontracting company may pay the actual worker.

## Client

The company or primary staffing vendor that Icon invoices.

Relevant information may include:
- legal/business name,
- billing contacts,
- billing email,
- payment terms,
- billing requirements,
- active consultants.

The invoice recipient may not always be the same as the end client where the consultant physically works.

## Engagement

The commercial relationship connecting:
- consultant,
- client,
- role/project,
- client bill rate,
- contractor pay rate,
- start date,
- billing frequency,
- payment terms,
- and engagement status.

This is the key source of truth for determining the correct rates and billing relationship.

## Timesheet

Evidence of the consultant's approved work for a billing period.

Relevant information includes:
- consultant,
- client,
- billing period,
- daily or total hours,
- total approved hours,
- approval status,
- approving manager when available,
- source email,
- source attachment,
- whether it has already been processed,
- whether a correction exists.

## Client Invoice

The amount Icon bills the client.

Relevant information includes:
- client,
- consultant/engagement,
- service period,
- approved hours,
- bill rate,
- total,
- invoice number,
- issue date,
- payment terms,
- due date,
- status,
- associated timesheet,
- sent date,
- payment status.

Formula:

`Client invoice amount = approved hours × client bill rate`

## Contractor Payment

The amount Icon owes the consultant or subcontracting vendor.

Relevant information includes:
- consultant/vendor,
- engagement,
- billing period,
- approved hours,
- contractor pay rate,
- amount owed,
- payment timing,
- payment status.

Formula:

`Contractor payment amount = approved hours × contractor pay rate`

## Billing Email

The message sent to the client's billing contact.

Typically includes:
- invoice,
- approved timesheet,
- billing period,
- consultant reference,
- standard billing message.

## Workflow Status

The business needs visibility into where each timesheet/billing event currently stands.

Useful conceptual statuses include:
- waiting for timesheet,
- timesheet received,
- needs review,
- validated,
- invoice prepared,
- invoice sent,
- awaiting client payment,
- client paid,
- contractor payment calculated,
- contractor payment pending,
- contractor paid,
- closed.

The exact labels can vary.

## Exception

Any case where the normal workflow cannot proceed confidently.

Common exceptions:
- missing approval,
- missing consultant match,
- missing client match,
- ambiguous billing period,
- hours mismatch,
- missing rate,
- duplicate timesheet,
- corrected timesheet,
- unreadable attachment,
- unexpected billing frequency,
- inconsistent client or consultant information.
