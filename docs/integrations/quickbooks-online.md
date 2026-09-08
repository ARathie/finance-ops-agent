# QuickBooks Online

Icon uses QuickBooks Desktop today and plans to move to QuickBooks Online (QBO). The agent needs an accounting system it can create invoices in and read paid status from; QBO has a programming interface for that and Desktop does not. Until the move happens, the agent works in **manual mode**: it makes and numbers the invoice PDF, and Kevin types the invoice into QuickBooks Desktop as he does today. Everything else (checks, emails, payment instructions, tracking sheet) works the same in both modes. Check endpoint details against the current Intuit developer documentation when implementing.

## Manual mode (`FOPS_ACCOUNTING=manual`)

- The agent assigns invoice numbers itself from a counter in its database with a prefix Kevin chooses (default `ICON-2026-0001`), renders the PDF from its own template (Icon's name and address, the client's legal name, the one line, hours, rate, total, invoice date, due date, payment terms, PO number if any), and attaches it to the billing email.
- Kevin enters the invoice into QuickBooks Desktop himself. The tracking sheet shows the agent's number; Kevin can use the same number in QuickBooks.
- Paid status: Kevin can reply to the Monday summary with "paid: ICON-2026-0001" if he wants the tracking sheet to show it; the agent does not chase.

## QuickBooks Online mode (`FOPS_ACCOUNTING=quickbooks`)

### One-time setup

1. Icon finishes the Desktop → Online move. The Customers list in QBO must contain every client, with names matching the "QuickBooks customer" column of the engagement list. The agent never creates customers.
2. Create one service item, for example "Consulting Services", and note its name (`QBO_ITEM_NAME`). Create the payment terms used (Net 30 etc.).
3. Create an app in the Intuit developer portal (accounting scope). Use its sandbox company first. Store the client id and secret in `.env`.
4. Run `fops qbo-connect`: it opens the Intuit sign-in page in a browser, Kevin (or the developer, with Kevin present) signs in and approves, and the local callback stores the realm id and tokens in `data/qbo_tokens.json` (restricted permissions).
5. `fops doctor` reads the company info, lists customers, and checks that every "QuickBooks customer" name in the engagement list exists.

### Tokens

Access tokens last one hour; refresh tokens rotate on use and expire after about 100 days of not being used. Always store the newly returned refresh token before using the new access token (write to a temp file, then rename). If refreshing fails, raise `QUICKBOOKS_RECONNECT` and email Kevin the steps (`fops qbo-connect` again). Warn at 80 days since the last successful refresh.

### Creating an invoice

QBO has no "draft" invoices, so the agent creates the invoice only at the moment the item is approved (ask first) or passes the guardrails (automatic), never before.

`POST /v3/company/{realmId}/invoice` with:

- `CustomerRef` looked up by the engagement list's "QuickBooks customer" name (looked up once per run, cached);
- one line: `SalesItemLineDetail` with `ItemRef` = the service item, `Qty` = approved hours, `UnitPrice` = bill rate, `Description` = "consultant — role — period" (and the PO number if the client's row has one);
- `TxnDate` = today in Icon's timezone; `DueDate` = TxnDate + payment terms days (also `SalesTermRef` when the terms exist in QBO);
- `CustomerMemo` = short note if any; `PrivateNote` = the agent's item id and invoice attempt (this is how the agent finds the invoice again after a crash);
- `BillEmail` = the billing email; `EmailStatus` = `NotSet` (the agent sends the email itself; QBO's own send is never used, or the client would get two emails);
- no sales tax (`TaxCodeRef` NON) unless Kevin says otherwise.

After the create call: read back `Id`, `DocNumber`, `TotalAmt`, `SyncToken`. `TotalAmt` must equal the agent's stored amount to the cent; if not, void the invoice immediately and raise `QUICKBOOKS_FAILED` with the details. Store `Id` and `DocNumber` on the invoice row. Fetch the PDF with `GET /v3/company/{realmId}/invoice/{Id}/pdf` and attach it to the billing email.

Restart safety: an `outgoing` row of kind `create_invoice` that is `in_flight` is reconciled by querying `SELECT * FROM Invoice WHERE CustomerRef = '<id>' AND TxnDate >= '<yesterday>'` and matching `PrivateNote` to the item id before creating anything. (Intuit also offers a `requestid` parameter for idempotent creates; use it as well if available, but do not rely on it alone.)

### Paid status

Once a day: for every invoice the agent created that is not yet paid, `GET /v3/company/{realmId}/invoice/{Id}` and read `Balance`. `Balance == 0` → the item becomes `client_paid` (date = today). Partial payments leave the status unchanged but the summary shows the remaining balance. The agent never records payments in QBO; Kevin or the bookkeeper does that as today.

### Cancelling

When Kevin cancels an item after the invoice was created, or accepts a corrected timesheet after the invoice was sent: `POST /v3/company/{realmId}/invoice?operation=void` with the invoice `Id` and current `SyncToken`. The replacement invoice is a new create whose `PrivateNote` notes which invoice it replaces.

### Sandbox first

All development and the first ask-first runs use a sandbox company (separate realm id and tokens). Switching to production is a settings change plus `fops qbo-connect` against the real company.

## Errors

- 401 → refresh the token once, then `QUICKBOOKS_RECONNECT`.
- 429 / 5xx → retry with backoff, at most 3 attempts across runs, then `QUICKBOOKS_FAILED`.
- Validation errors (400) → `QUICKBOOKS_FAILED` at once with the message from QBO (usually a missing customer or item).

## Testing

Recorded JSON responses for create, read-back with matching and mismatching totals, PDF fetch, balance check, void, and token refresh (including a rotated refresh token). A sandbox smoke test behind `FOPS_LIVE_TESTS=1`.
