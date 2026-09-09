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
| Which server will the agent run on for real? | A 1 vCPU / 1 GB Ubuntu LTS virtual machine at DigitalOcean, Hetzner, or AWS Lightsail, running the agent as a container (`running-it.md` stage 2). The Mac is for the first test only. |
| Who holds Icon's Rackspace Email admin login, to create the agent's mailbox? | Kevin, or whoever set up Icon's email. |
| Will the agent's mailbox be a standard Rackspace Email (IMAP) mailbox like Kevin's, not Hosted Exchange? | Rackspace Email, the same kind as Kevin's. |
| Does Rackspace offer an app-specific password or two-step verification for the agent's mailbox? | Use it if offered; otherwise a long random password. |
| Who gets the heartbeat alert when the agent stops checking in? | Ash and Kevin. |
| How does Kevin's engagement list reach the server? | A shared folder (OneDrive, Dropbox, or Google Drive) the server pulls from before every run; a copy by hand as the fallback. |
| Where should backups go? | An object-storage bucket the business controls (Backblaze B2, or the server provider's own storage), uploaded nightly; OneDrive/SharePoint only during the Mac test. |
| What should the Monday summary include or leave out? | Everything listed in `emails.md`. |
