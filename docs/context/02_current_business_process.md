# Current Business Process

## End-to-End Staffing Process

The broader staffing process works approximately as follows.

### 1. Receive client staffing request

The client provides some combination of:
- role title,
- job description,
- required skills,
- experience level,
- location,
- expected start date,
- bill-rate range,
- work authorization constraints,
- and other role-specific requirements.

**Current state: Manual**

### 2. Search job boards and candidate databases

Recruiters search sources such as:
- Dice,
- LinkedIn,
- Indeed,
- internal candidate lists,
- staffing-partner lists.

**Current state: Manual**

### 3. Send candidate outreach

Recruiters contact a broad set of potentially relevant candidates.

Typical information requested includes:
- interest,
- availability,
- current location,
- willingness to relocate,
- expected rate,
- work authorization,
- resume,
- and whether another staffing company is already representing the candidate.

**Current state: Manual**

### 4. Review interested candidates

Recruiters review responses and resumes and decide which candidates appear suitable.

**Current state: Manual**

### 5. Collect missing candidate information

Recruiters follow up for any missing information required before submission.

**Current state: Manual**

### 6. Conduct brief qualification review

Recruiters may conduct a short call to:
- verify experience,
- understand recent projects,
- confirm availability and rate,
- identify obvious resume inconsistencies,
- and check for major red flags.

**Current state: Manual**

### 7. Submit selected candidates to the client

Icon sends the client the selected candidate's resume and relevant information.

**Current state: Manual**

### 8. Coordinate interviews and selection

Icon coordinates client interviews, feedback, candidate communication, and final selection.

**Current state: Manual**

### 9. Execute contracts and onboarding

Once a consultant is selected:
- client paperwork is finalized,
- consultant or subcontractor agreements are finalized,
- start date and onboarding details are confirmed.

**Current state: Manual**

---

# Operational Workflow This Project Is Optimizing

The active project begins after the consultant is already working.

## 10. Consultant records time with the client

The consultant enters time into whatever system the client uses.

This varies by client.

Examples include:
- a client timekeeping system,
- a vendor-management system such as Fieldglass,
- or another client-specific process.

The client manager or supervisor approves the consultant's hours.

## 11. Collect approved timesheets by email

The consultant sends Icon a copy of the approved timesheet.

The timesheet may arrive as:
- PDF,
- spreadsheet,
- system-generated report,
- screenshot or image,
- or another attachment format.

Different clients may generate different layouts.

Icon currently receives these through email.

**Current state: Highly manual**

Current pain points include:
- consultants submitting on different days,
- delayed manager approvals,
- missing timesheets,
- inconsistent formats,
- duplicate submissions,
- and manually determining whether the submitted document is actually approved.

## 12. Create the client invoice

Icon uses the approved hours and the client's agreed bill rate to create an invoice.

The current process in QuickBooks Desktop involves manually:
- finding or duplicating a prior invoice,
- entering the billing period,
- entering approved hours,
- applying the client's bill rate,
- updating invoice-identifying information,
- saving the invoice,
- and associating the approved timesheet with the billing process.

The core calculation is:

`Client invoice amount = approved hours × client bill rate`

**Current state: Highly manual**

## 13. Email the invoice to the client

Icon sends the client:
- the Icon invoice,
- and the approved consultant timesheet.

This is typically done by email.

The invoice must go to the correct billing contact and correspond to:
- the correct consultant,
- correct client,
- correct billing period,
- correct hours,
- correct rate.

Icon then tracks whether the invoice has been paid.

**Current state: Highly manual**

## 14. Calculate contractor pay

Icon separately determines what the consultant or subcontracting company should be paid.

The core calculation is:

`Contractor pay = approved hours × contractor pay rate`

The contractor pay rate is different from the client bill rate.

Example:

- Client bill rate: $140/hour
- Contractor pay rate: $100/hour
- Approved hours: 156

Then:
- Client invoice = $21,840
- Contractor pay = $15,600

These calculations should be based on approved business records, not inferred from arbitrary emails.

**Current state: Manual**

## 15. Pay contractor

Historically, contractor payments have been made through bank ACH or other payroll/payment systems depending on the worker type.

The current project does **not** need to automatically release money without review.

## 16. Reconcile records

Icon tracks:
- what was invoiced,
- when the client paid,
- what the consultant was owed,
- what the consultant was paid,
- and year-end totals used for accounting and tax reporting.

Historically, this information has been split across:
- QuickBooks,
- spreadsheets,
- bank records,
- email,
- and tax-reporting tools.

**Current state: Manual and error-prone**
