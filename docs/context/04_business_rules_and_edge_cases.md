# Business Rules and Edge Cases

## Core Financial Rule

There are always two separate rates to keep distinct:

1. **Client bill rate**
   - What Icon charges the client.

2. **Contractor pay rate**
   - What Icon pays the consultant or subcontracting company.

These must never be confused.

Example:

- Approved hours: 156
- Client bill rate: $140/hour
- Contractor pay rate: $100/hour

Results:
- Client invoice: $21,840
- Contractor pay: $15,600

The difference is Icon's gross margin before other expenses.

## Trusted Rate Source

Rates should be based on the approved engagement or contract information.

The workflow should not treat an arbitrary value mentioned in an email as authoritative if it conflicts with the trusted engagement record.

## Timesheet Approval

A timesheet should only be considered billable if it is sufficiently clear that the client has approved the hours.

Approval may appear differently depending on the client and timesheet system.

If approval cannot be established confidently, the workflow should treat it as an exception rather than silently proceeding.

## Duplicate Prevention

The same timesheet may be:
- emailed twice,
- forwarded,
- resent after a delay,
- attached to multiple messages,
- or encountered again after the agent restarts.

The same consultant/billing-period work should not accidentally create duplicate invoices.

The business expectation is:

> One approved billing event should produce one intended invoice unless there is a deliberate correction or replacement.

## Multiple Consultants for One Client

A client may have several consultants working through Icon.

Consultants may submit timesheets on different dates.

Do not assume all consultants for a client will be ready for billing at the same time.

Historically, Icon often created one invoice per consultant because this simplified tracking.

## Billing Frequency

Most current consultant billing is monthly.

Some engagements may use different schedules, including biweekly or other client-specific terms.

The workflow should not assume every engagement has the same billing cycle.

## Client-Specific Timesheet Systems

Consultants may submit time through different client systems.

The output can vary substantially.

Examples:
- summary report,
- detailed daily report,
- spreadsheet,
- PDF,
- screenshot,
- vendor-management-system export.

The formatting is not standardized.

## Consultant Types

The person performing the work may be:
- an Icon W-2 employee,
- an independent contractor,
- or an employee/contractor of another consulting company.

The contractor-pay workflow may differ depending on the relationship.

The system should not assume every consultant is a direct individual 1099 contractor.

## Multi-Layer Staffing Relationships

Sometimes Icon is not contracting directly with the end client.

Example:

`End Client -> Primary Staffing Vendor -> Icon -> Consultant`

The party that Icon invoices may therefore be a primary vendor rather than the actual end client where the consultant performs work.

The invoice recipient must be based on the actual contractual billing relationship.

## Missing or Late Timesheets

Common real-world cases include:
- consultant forgets to send the timesheet,
- manager has not approved it yet,
- consultant is on leave,
- only some consultants for a client are ready,
- incorrect billing period is sent.

These should be treated as normal workflow exceptions.

## Corrected Timesheets

A consultant may send a corrected version after an earlier submission.

A corrected timesheet should not simply create another invoice.

The system should recognize that a replacement or correction may require review of the existing invoice/status.

## Invoice Tracking

For each billing event, Icon ultimately needs to know:
- whether the timesheet was received,
- whether it was validated,
- whether an invoice was created,
- whether the invoice was sent,
- invoice amount,
- payment terms,
- whether the client paid,
- how much the contractor was owed,
- and whether the contractor was paid.

## Human Review

Financial mistakes are materially worse than requiring occasional human review.

If the system is uncertain about:
- identity,
- hours,
- approval,
- client,
- rates,
- billing period,
- duplicate status,
- or invoice recipient,

the business preference is to flag the item for review rather than guess.

## Accounting Context

Icon currently uses QuickBooks Desktop.

The project team is considering migration to QuickBooks Online to support the desired invoice workflow.

The coding agent should understand this as business context, not as a mandatory implementation instruction.

The essential business requirement is that invoices created by the workflow are correctly represented in Icon's accounting records.
