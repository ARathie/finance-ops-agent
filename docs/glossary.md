# Glossary

Plain definitions of the words used across these docs. If a word here conflicts with how the business describes itself in `context/`, the business description wins.

- **The agent** — the software this project builds. It has its own mailbox (for example `jay@icon-technologies.com`), reads the timesheets consultants send, prepares invoices, and emails Kevin and clients. It never pays anyone.
- **Kevin / the administrator** — the person at Icon who runs billing day to day (`kevin@icon-technologies.com`). Every email the agent sends about a timesheet goes to him or copies him.
- **Consultant** — a person doing work for a client through Icon. May be an Icon employee, an independent contractor, or someone supplied by a vendor company.
- **Vendor company** — another consulting company that supplies a consultant to Icon. When a consultant comes through a vendor, Icon pays the vendor, and the vendor pays the consultant.
- **Client** — the company Icon sends the invoice to. Sometimes this is a staffing company sitting between Icon and the place where the consultant actually works.
- **End client** — where the consultant actually works, when that is not the same company as the client Icon invoices.
- **Engagement** — one consultant working for one client: the role, start and end dates, billing schedule, bill rate, and pay rate.
- **Engagement list** — the spreadsheet Kevin keeps with every client, consultant, vendor, and engagement. It is the only place rates come from. See `engagement-list.md`.
- **Bill rate** — what Icon charges the client per hour.
- **Pay rate** — what Icon pays the consultant (or vendor company) per hour. Always a different number from the bill rate; the two are never mixed up.
- **Billing period** — the stretch of dates one invoice covers, for example the month of August, or the two weeks ending August 15. Set per engagement by its billing schedule.
- **Billing schedule** — how often an engagement is invoiced: monthly, twice a month, every two weeks, or weekly.
- **Timesheet** — the file a consultant emails showing the hours they worked in a period, usually a printout or export from the client's own time system. Formats vary a lot.
- **Approved** — the client's manager has signed off on the hours. The agent looks for an "Approved" status, an approver's name and date, a signature, or a forwarded approval email from the client. A consultant saying "these are approved" is not enough on its own.
- **Approved hours** — the total hours on an approved timesheet. Both the invoice and the consultant's pay are hours times a rate.
- **Billing contact** — the person (or mailbox) at the client who receives invoices.
- **Payment terms** — how many days the client has to pay after the invoice date, for example 30 days.
- **Timesheet item** — the agent's record for one consultant's timesheet for one billing period, with its status. See `status-tracking.md`.
- **Review item** — a note from the agent that something is unclear and needs Kevin. See `timesheet-checks.md`.
- **Tracking sheet** — the Excel file the agent keeps up to date with one row per timesheet item, for Kevin's records.
- **Payment instruction** — the email telling Kevin what a consultant or vendor is owed for a period and when it is due. Kevin makes the payment himself.
- **Dry run / Ask first / Automatic** — the three modes the agent can run in. See `how-it-works.md`.
- **QuickBooks Online (QBO)** — the online version of QuickBooks the business plans to move to. Once connected, the agent creates invoices there directly and can see when they are paid.
- **Manual QuickBooks entry** — until QuickBooks Online is connected, the agent makes the invoice PDF and Kevin types the invoice into QuickBooks Desktop himself, as today.
- **Microsoft 365** — where the agent's mailbox lives. The agent reads and sends mail through Microsoft's programming interface (Microsoft Graph).
- **Claude** — the AI model (from Anthropic) the agent uses to read timesheets in whatever format they arrive.
