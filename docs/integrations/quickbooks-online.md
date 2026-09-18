# QuickBooks Online

Icon uses QuickBooks Desktop today and plans to move to QuickBooks Online (QBO). The agent needs an accounting system it can create invoices in and read paid status from; QBO has a programming interface for that and Desktop does not. Until the move happens, the agent works in **manual mode**: it makes and numbers the invoice PDF, and Kevin types the invoice into QuickBooks Desktop as he does today. Everything else (checks, emails, payment instructions, tracking sheet) works the same in both modes. Check endpoint details against the current Intuit developer documentation when implementing.

## Manual mode (`FOPS_ACCOUNTING=manual`)

- The agent numbers the invoice in Kevin's format, `<MMDDYY><client code>-<consultant code>` (`083126MT-PS`, see `engagement-list.md`), renders the PDF from its own template (Icon's name and address, the client's legal name, the one line, hours, rate, total, invoice date, due date, payment terms, PO number if any), and attaches it to the billing email.
- Kevin enters the invoice into QuickBooks Desktop himself. The tracking sheet shows the agent's number; Kevin can use the same number in QuickBooks.
- Paid status: Kevin can reply to the Monday summary with "paid: 083126MT-PS" if he wants the tracking sheet to show it; the agent does not chase.

## QuickBooks Online mode (`FOPS_ACCOUNTING=quickbooks`)

### One-time setup

This is the developer's half of it. `docs/quickbooks-setup.md` is the same setup written for Kevin -- what to click, and the `fops doctor` line that names each thing when it is wrong. Change both together (`CLAUDE.md`, definition of done).

1. Icon finishes the Desktop → Online move. The Customers list in QBO must contain every client, with names matching the "QuickBooks customer" column of the engagement list. The agent never creates customers.
2. **One product per engagement** -- that is, per consultant *per client*. The product is named for the consultant and sits under a **category named for the client**, so QuickBooks knows it as `MasTec:Sridhar Doraiswamy`, which is what the agent looks it up by (decision 36). The bill rate for that engagement goes on the product. A consultant at two clients has two products under two categories, which is the only way two rates can be held. The rate on the product is what the client is billed (decision 30); an engagement with no product, a product found by the client's other name, a product not yet under a category, two products of one name with no category to tell them apart (refused), or a product with no rate, is refused rather than guessed at. Create the payment terms used (Net 30 etc.).
3. Put each engagement's **pay rate on the purchase side of its product** (tick "I purchase this product/service from a vendor", then the cost and the preferred vendor). That is what the agent tells Kevin to pay (decision 38); where it is blank the engagement list is used instead.
4. Turn on **Settings -> Account and settings -> Sales -> Sales form content -> Custom transaction numbers**. Without it QuickBooks numbers invoices from its own counter and ignores the number the agent sends; the agent checks the number that comes back and voids anything numbered otherwise (decision 27).
5. Create an app in the Intuit developer portal (accounting scope). Use its sandbox company first. Store the client id and secret in `.env`.
6. Run `fops qbo-connect`: it prints and opens the Intuit sign-in page, Kevin (or the developer, with Kevin present) signs in and approves, and a one-shot loopback server on `http://localhost:8723/callback` (register that redirect URI in the Intuit app; `--port` changes it) stores the realm id and tokens in `data/qbo_tokens.json` with owner-only permissions. The `state` value is checked, so another tab's redirect cannot be mistaken for this one.
7. `fops doctor` then reports two more checks: **quickbooks connection** (connected, and the refresh token is not near expiry) and **quickbooks customers** (every active client's "QuickBooks customer" name — or its legal name where that column is blank — exists in QuickBooks),, **quickbooks products** (every active engagement has a product, under a category named for its client, whose rate agrees with the engagement list), and **quickbooks pay rates** (where a product's purchase side is filled in, what QuickBooks pays and who it pays agree with the engagement list -- read but not used yet, decision 37). In manual mode they are skipped with a line saying so.

### Tokens

Access tokens last one hour; refresh tokens rotate on use and expire after about 100 days of not being used. Always store the newly returned refresh token before using the new access token (write to a temp file, then rename). If refreshing fails, raise `QUICKBOOKS_RECONNECT` and email Kevin the steps (`fops qbo-connect` again). Warn at 80 days since the last successful refresh.

### Creating an invoice

QBO has no "draft" invoices. The agent creates the invoice **before** asking Kevin to approve it, so that the PDF he approves is the one the client will receive -- his own invoice template, priced from the consultant's product (decision 33). Cancelling therefore voids it, and the voided invoice stays in the books. Dry run still creates nothing at all.

`POST /v3/company/{realmId}/invoice` with:

The product is fetched with `SELECT * FROM Item WHERE FullyQualifiedName = '<client>:<consultant>'` -- the whole entity, not a field list. QuickBooks' query language refuses `PrefVendorRef` in a `SELECT` ("Property PrefVendorRef not found for Entity Item"): references come back with the entity or not at all.

- `CustomerRef` looked up by the engagement list's "QuickBooks customer" name, against `DisplayName` and then `CompanyName` (decision 32; looked up once per run, cached). A company name shared by several customers is refused rather than guessed between;
- `DocNumber` = the number the agent worked out (decision 27). QuickBooks honours it only with custom transaction numbers on, and refuses a number another invoice already has (Intuit error 6140) -- which is what should happen, because a repeat means a bug, not something to wave through with `include=allowduplicatedocnum`;
- one line: `SalesItemLineDetail` with `ItemRef` = **the engagement's product** (found by `FullyQualifiedName`, `<client>:<consultant>`), `Qty` = approved hours, `UnitPrice` = the rate read off that product, `ServiceDate` = the **end of the billing period** (the column Kevin's invoice template labels "period ending"), `Description` = the consultant's name (the product is the consultant);
- `TxnDate` = today in Icon's timezone; `DueDate` = TxnDate + payment terms days (also `SalesTermRef` when the terms exist in QBO);
- `CustomerMemo` = short note if any; `PrivateNote` = the agent's item id and invoice attempt (this is how the agent finds the invoice again after a crash);
- `BillEmail` = the billing email; `EmailStatus` = `NotSet` (the agent sends the email itself; QBO's own send is never used, or the client would get two emails);
- no sales tax (`TaxCodeRef` NON) unless Kevin says otherwise.

After the create call: read back `Id`, `DocNumber`, `TotalAmt`, `SyncToken`. `DocNumber` must be the number that was asked for and `TotalAmt` must equal the agent's stored amount to the cent; if either disagrees, void the invoice immediately and raise `QUICKBOOKS_FAILED` with the details (a `DocNumber` QuickBooks chose itself says custom transaction numbers are off, and the message says so). Store `Id` and `DocNumber` on the invoice row. Fetch the PDF with `GET /v3/company/{realmId}/invoice/{Id}/pdf` and attach it to the billing email.

Restart safety: an `outgoing` row of kind `create_invoice` that is `in_flight` is reconciled by querying `SELECT * FROM Invoice WHERE CustomerRef = '<id>' AND TxnDate >= '<yesterday>'` and matching `PrivateNote` to the item id before creating anything. (Intuit also offers a `requestid` parameter for idempotent creates; use it as well if available, but do not rely on it alone.)

### Paid status

Once a day: for every invoice the agent created that is not yet paid, `GET /v3/company/{realmId}/invoice/{Id}` and read `Balance`. `Balance == 0` → the item becomes `client_paid` (date = today). Partial payments leave the status unchanged but the summary shows the remaining balance. The agent never records payments in QBO; Kevin or the bookkeeper does that as today.

### Cancelling

When Kevin cancels an item after the invoice was created, or accepts a corrected timesheet after the invoice was sent: the invoice is **renamed first** with a sparse update (`POST /v3/company/{realmId}/invoice` with `Id`, `SyncToken`, `sparse: true`, `DocNumber` = `<number>-VOID`) and **then** voided with `POST /v3/company/{realmId}/invoice?operation=void` and the sync token the rename handed back. The rename is what gives the number back, since QuickBooks will not reuse one a voided invoice still holds (decision 34); a rename that fails is logged and the void goes ahead anyway. The replacement invoice is a new create whose `PrivateNote` notes which invoice it replaces.

### Sandbox first

All development and the first ask-first runs use a sandbox company (separate realm id and tokens). Switching to production is a settings change plus `fops qbo-connect` against the real company.

## Proving the create path: `fops qbo-test-invoice`

The ordinary flow cannot test this. `dry_run` creates nothing in QuickBooks, on purpose, so the only other way in is Kevin approving an invoice -- and approving sends the billing email to whatever address the client's row holds. That makes "did the invoice come out right?" and "can anything reach a client?" the same question, which they should not be.

```
fops qbo-test-invoice --consultant "Priya Shah" --client "Acme Corp" \
    --period-end 2026-08-31 --hours 156.00
```

It goes through the real code -- the engagement list, the billing period from the engagement's schedule, the rate row in force, the invoice number, the customer and product lookups, the create, the checks on what came back, the PDF -- and stops there. No mailbox is opened, no email is written, nothing is recorded in the agent's own database.

- **It removes what it made.** By default the invoice is deleted, so a company Icon is not yet using is left exactly as it was and the number is free again. `--cleanup void` leaves the voided record instead (which is what a real correction does, and the number stays spent); `--cleanup keep` leaves the invoice there to be looked at.
- **A number QuickBooks already has is stepped past**, `-2` then `-3`, the same way a replacement invoice does. That is what a run after `--cleanup void` or `--cleanup keep` will do.
- **The PDF is written to a file** (`--pdf`, otherwise into the data folder), so the invoice template can be looked at without sending anything.
- The private note carries a marker that is different every run, so an invoice left behind by an earlier run is never mistaken for this one's. `--item-id` fixes it if you want to test the crash-recovery lookup.

If it fails, the reason is printed and the JSON log holds the whole exchange, including the text of anything QuickBooks refused.

## Errors

Every failure becomes a review item emailed to Kevin, never an end to the run: the other timesheets are still read, their emails still go out, and the invoice is made on a later run once the problem is fixed. `QUICKBOOKS_RECONNECT` is raised once per run rather than once per item — asking again for each one would mean another attempt at the token endpoint each time.

Intuit puts a trace id in the `intuit_tid` response header. It is logged on every call and repeated in whatever the agent reports, because it is the first thing Intuit's support asks for.

- 401 → refresh the token once, then `QUICKBOOKS_RECONNECT`.
- 429 / 5xx → retry with backoff, at most 3 attempts across runs, then `QUICKBOOKS_FAILED`.
- Validation errors (400) → `QUICKBOOKS_FAILED` at once with the message from QBO (usually a missing customer or item).

## Testing

Recorded JSON responses in `tests/fixtures/qbo/`, replayed through a real `httpx` client so the adapter's own code runs (`tests/contract/test_quickbooks.py`): a create whose total agrees, a create whose total does **not** agree (voided, with both amounts in the message), a create QuickBooks numbered itself (voided, naming the setting to turn on), finding an invoice by its private note after a crash, a customer found by its company name where its display name is a person, a company name shared by two customers (refused), a missing customer, a consultant with no product, a product whose rate has drifted from the engagement list (voided, with both totals), balances (paid, partly paid, unpaid), a void that renames the invoice first and voids with the sync token that came back, a rename that fails without stopping the void, a voided invoice not being mistaken for a live one after a crash, a token refresh that rotates the refresh token, a refused refresh, and a 401 that one refresh fixes. No network, no credentials, no real company.

Two facts worth keeping straight in tests and in code: the invoice **number** (`DocNumber`) is what Kevin and the client see, and the **external id** (`Id`) is what QuickBooks and the agent's own invoice rows key on. In manual mode they happen to be the same string; in QuickBooks Online they are not. The number itself is the agent's in both modes (decision 27), so it reads the same either side of the move.

A sandbox smoke test behind `FOPS_LIVE_TESTS=1` is still to come; the first real exercise is ask first mode against the sandbox company (see the roadmap).
