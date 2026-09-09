# Open questions for Kevin

Things only Icon can answer. Until they are answered, the agent will use the default in the second column. Each answer goes into the engagement list or the settings; none of them changes the design.

| Question | Default until answered |
|---|---|
| What timezone should "today" and due dates use? | Must be set before the first run (no default). |
| Payment terms for each client? | 30 days. |
| Wording of the billing email, and who else at Icon should be copied? | The template in `emails.md`; only Kevin is copied. |
| Who approves invoices and answers reviews? | Kevin, from `kevin@icon-technologies.com`. |
| What is the agent's mailbox address? | `jay@icon-technologies.com`. |
| Which time system does each client use, and what does "approved" look like in it? | The agent looks for the approval signs listed in `timesheet-checks.md`; a screenshot or export from each client's system would let us test them. |
| Does any client need a PO number on the invoice, or upload to a portal instead of email? | No PO numbers; every client gets the invoice by email. |
| How many days after the end of the period is each consultant or vendor paid? | 15 days for consultants, 30 for vendor companies. |
| Do vendor companies send Icon their own invoices? | The payment instruction shows the expected amount; Kevin compares it with the vendor's invoice if there is one. |
| When will the move to QuickBooks Online happen, and are customer names in QuickBooks the same as in the engagement list? | Manual mode (the agent makes the PDF, Kevin enters the invoice) until the move is done. |
| Any sales tax on invoices? | None. |
| Invoice numbering: should the agent continue Kevin's current sequence? | In manual mode `ICON-<year>-<number>` starting from a number Kevin chooses; in QuickBooks Online mode, QuickBooks assigns the number. |
| Can Kevin share a few real timesheets (with names changed) to test with? | Made-up timesheets only until then. |
| Which machine will the agent run on? | An always-on Mac at Icon, checking mail every 15 minutes (launchd, and sleep disabled). Answered: macOS. |
| Where should backups go? | A folder on OneDrive/SharePoint, copied nightly. |
| What should the Monday summary include or leave out? | Everything listed in `emails.md`. |
