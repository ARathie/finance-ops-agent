# QuickBooks Online

Icon uses QuickBooks Desktop today and plans to move to QuickBooks Online (QBO). The agent needs an accounting system it can create invoices in and read paid status from; QBO has a programming interface for that and Desktop does not. Until the move happens, the agent works in **manual mode**: it makes and numbers the invoice PDF, and Kevin types the invoice into QuickBooks Desktop as he does today. Everything else (checks, emails, payment instructions, tracking sheet) works the same in both modes. Check endpoint details against the current Intuit developer documentation when implementing.

## Manual mode (`FOPS_ACCOUNTING=manual`)

- The agent numbers the invoice in Kevin's format, `<MMDDYY><client code>-<consultant code>` (`083126MT-PS`, see `engagement-list.md`), renders the PDF from its own template (Icon's name and address, the client's legal name, the one line, hours, rate, total, invoice date, due date, payment terms, PO number if any), and attaches it to the billing email.
- Kevin enters the invoice into QuickBooks Desktop himself. The tracking sheet shows the agent's number; Kevin can use the same number in QuickBooks.
- Paid status: Kevin can reply to the Monday summary with "paid: 083126MT-PS" if he wants the tracking sheet to show it; the agent does not chase.

## QuickBooks Online mode (`FOPS_ACCOUNTING=quickbooks`)

### One-time setup

This is the developer's half of it. `docs/quickbooks-setup.md` is the same setup written for Kevin -- what to click, and the `fops doctor` line that names each thing when it is wrong. Change both together (`CLAUDE.md`, definition of done).

1. Icon finishes the Desktop → Online move. The Customers list in QBO must contain every client, with names matching the "QuickBooks customer" column of the engagement list. The agent never creates customers on its own; see "Setting up a new engagement" below for the one way it does.
2. **One product per engagement** -- that is, per consultant *per client*. The product is named for the consultant and sits under a **category named for the client**, so QuickBooks knows it as `MasTec:Sridhar Doraiswamy`, which is what the agent looks it up by (decision 36). The bill rate for that engagement goes on the product. A consultant at two clients has two products under two categories, which is the only way two rates can be held. The rate on the product is what the client is billed (decision 30); an engagement with no product, a product found by the client's other name, a product not yet under a category, two products of one name with no category to tell them apart (refused), or a product with no rate, is refused rather than guessed at. Create the payment terms used (Net 30 etc.).
3. **Making a product inactive is how an engagement ends.** The agent lists the active products under a category and expects timesheets for those, so QuickBooks is the switch rather than the engagement list's own `Active` column (decision 42). The row stays on the list: the billing schedule and the start date live only there, so a product with no row behind it becomes a LIST_ROW_PROBLEM review rather than a guessed schedule. An empty listing means "not set up yet", never "everything has ended", and the engagement list decides on its own.
4. Put each engagement's **pay rate on the purchase side of its product** (tick "I purchase this product/service from a vendor", then the cost and the preferred vendor). That is what the agent tells Kevin to pay (decision 38); where it is blank the engagement list is used instead.
5. Turn on **Settings -> Account and settings -> Sales -> Sales form content -> Custom transaction numbers**. Without it QuickBooks numbers invoices from its own counter and ignores the number the agent sends; the agent checks the number that comes back and voids anything numbered otherwise (decision 27).
6. Create an app in the Intuit developer portal (accounting scope). Icon connects it with the **production** keys, straight to its own company (decision 51). Store the client id and secret in `.env` with `QBO_ENVIRONMENT=production`.
7. Register the redirect URI. A production app may only redirect to an https address that is not localhost. Publish `docs/public-site/callback.html` alongside the other public pages, add its address under the app's production **Redirect URIs**, and put the same address in `QBO_REDIRECT_URI`. A sandbox app can instead leave `QBO_REDIRECT_URI` blank and register `http://localhost:8723/callback`; `--port` changes the port.
8. Run `fops qbo-connect`. It prints and opens the Intuit sign-in page, and Kevin (or the developer, with Kevin present) signs in and approves. With `QBO_REDIRECT_URI` set, the browser lands on the callback page and the person pastes the address it shows into the terminal. With it blank, a one-shot listener on localhost catches the redirect. Either way the `state` value is checked, so another sign-in's redirect cannot be mistaken for this one, and the realm id and tokens go in `data/qbo_tokens.json` with owner-only permissions. Production with no `QBO_REDIRECT_URI` is refused before anything opens.
9. `fops doctor` then reports six more checks: **quickbooks connection** (connected, to the kind of company `QBO_ENVIRONMENT` names, and the refresh token is not near expiry), **quickbooks customers** (every active client's "QuickBooks customer" name — or its legal name where that column is blank — exists in QuickBooks), **quickbooks products** (every active engagement has a product, under a category named for its client, whose rate agrees with the engagement list), **quickbooks pay rates** (where a product's purchase side is filled in, what QuickBooks pays and who it pays agree with the engagement list -- read but not used yet, decision 37), and **quickbooks engagements** (every live product under a category has a row on the engagement list to schedule it from, and every active row has a live product; decision 42), and **quickbooks contacts** (each client's customer record and each payee's vendor record -- the email and the payment terms -- agree with the engagement list; a client's addresses must be the same set on both sides, because QuickBooks' are the ones used, decision 52, while a payee's email is still only compared). In manual mode they are skipped with a line saying so.

### Without the engagement list (`FOPS_ENGAGEMENTS=quickbooks`)

`application/from_quickbooks.py` builds the same `EngagementWorkbook` the spreadsheet parses to, from QuickBooks alone (decision 53):

- each active product under a category is an engagement; its sales price and purchase cost are the two rates; its **`PurchaseDesc`** carries `Start:` and the optional `Schedule:`, `First period:`, `Send automatically:` lines (Item has no `Notes`);
- the category is looked up as a customer (display name, then company name, decision 32); the customer's `PrimaryEmailAddr` (comma-separated) and terms are where and when; its **`Notes`** carries `Invoice code:` and the optional `Delivery:`, `CC:`;
- the product's `PrefVendorRef` is who Icon pays (company name, else display name, decision 46), the vendor's terms are the pay timing, and the vendor's email is the consultant's timesheet address;
- paid by bank transfer, for everyone.

What cannot be built is a `ListRowProblem` on sheet `QuickBooks` with row 0, naming the record, and that engagement is left out. `AccountingFailed` from the listing itself makes the run fall back to the engagement list, with a line in the run report. The **quickbooks setup** doctor check runs this whatever the setting.

### When a field is missing that QuickBooks plainly shows

Intuit serves an old shape of each entity unless a minor version is asked for, and newer fields simply do not come back -- `Sku` is the documented case, and a product's category may be another. Nothing is sent by default, because every recording here and every company already working was answered without one. `fops qbo-show <customer|vendor|product|term> [name]` prints a record exactly as QuickBooks returns it, and `--minorversion 75` asks for a newer shape without changing anything; `FOPS_QBO_MINORVERSION` makes it permanent, in which case the recordings in `tests/fixtures/qbo/` have to be re-made against it, since it is part of every URL (decision 49).

### Tokens

Access tokens last one hour; refresh tokens rotate on use and expire after about 100 days of not being used. Always store the newly returned refresh token before using the new access token (write to a temp file, then rename). If refreshing fails, raise `QUICKBOOKS_RECONNECT` and email Kevin the steps (`fops qbo-connect` again). Warn at 80 days since the last successful refresh.

### Creating an invoice

QBO has no "draft" invoices. The agent creates the invoice **before** asking Kevin to approve it, so that the PDF he approves is the one the client will receive -- his own invoice template, priced from the consultant's product (decision 33). Cancelling therefore voids it, and the voided invoice stays in the books. Dry run still creates nothing at all.

`POST /v3/company/{realmId}/invoice` with:

The product is fetched with `SELECT * FROM Item WHERE FullyQualifiedName = '<client>:<consultant>'` -- the whole entity, not a field list. QuickBooks' query language refuses `PrefVendorRef` in a `SELECT` ("Property PrefVendorRef not found for Entity Item"): references come back with the entity or not at all.

- `CustomerRef` looked up by the engagement list's "QuickBooks customer" name, against `DisplayName` and then `CompanyName` (decision 32; looked up once per run, cached). A company name shared by several customers is refused rather than guessed between;
- `DocNumber` = the number the agent worked out (decision 27). QuickBooks honours it only with custom transaction numbers on, and refuses a number another invoice already has (Intuit error 6140) -- which is what should happen, because a repeat means a bug, not something to wave through with `include=allowduplicatedocnum`;
- one line: `SalesItemLineDetail` with `ItemRef` = **the engagement's product** (found by `FullyQualifiedName`, `<client>:<consultant>`), `Qty` = approved hours, `UnitPrice` = the rate read off that product (the same rate the agent priced the item with when the timesheet was read, decision 43), `ServiceDate` = the **end of the billing period** (the column Kevin's invoice template labels "period ending"), `Description` = the consultant's name (the product is the consultant);
- `TxnDate` = today in Icon's timezone; `DueDate` = TxnDate + payment terms days (also `SalesTermRef` when the terms exist in QBO);
- `CustomerMemo` = short note if any; `PrivateNote` = the agent's item id and invoice attempt (this is how the agent finds the invoice again after a crash);
- `BillEmail` = the billing email; `EmailStatus` = `NotSet` (the agent sends the email itself; QBO's own send is never used, or the client would get two emails);
- no sales tax (`TaxCodeRef` NON) unless Kevin says otherwise.

After the create call: read back `Id`, `DocNumber`, `TotalAmt`, `SyncToken`. `DocNumber` must be the number that was asked for and `TotalAmt` must equal the agent's stored amount to the cent; if either disagrees, void the invoice immediately and raise `QUICKBOOKS_FAILED` with the details (a `DocNumber` QuickBooks chose itself says custom transaction numbers are off, and the message says so). Since decision 43 the agent already priced the item from the product, so what the total check is left catching is the product's rate having been changed between the timesheet being read and Kevin approving -- a window that exists because the invoice is not made until he does. Store `Id` and `DocNumber` on the invoice row. Fetch the PDF with `GET /v3/company/{realmId}/invoice/{Id}/pdf` and attach it to the billing email.

Restart safety: an `outgoing` row of kind `create_invoice` that is `in_flight` is reconciled by querying `SELECT * FROM Invoice WHERE CustomerRef = '<id>' AND TxnDate >= '<yesterday>'` and matching `PrivateNote` to the item id before creating anything. (Intuit also offers a `requestid` parameter for idempotent creates; use it as well if available, but do not rely on it alone.)

### Paid status

Once a day: for every invoice the agent created that is not yet paid, `GET /v3/company/{realmId}/invoice/{Id}` and read `Balance`. `Balance == 0` → the item becomes `client_paid` (date = today). Partial payments leave the status unchanged but the summary shows the remaining balance. The agent never records payments in QBO; Kevin or the bookkeeper does that as today.

### Cancelling

When Kevin cancels an item after the invoice was created, or accepts a corrected timesheet after the invoice was sent: the invoice is **renamed first** with a sparse update (`POST /v3/company/{realmId}/invoice` with `Id`, `SyncToken`, `sparse: true`, `DocNumber` = `<number>-VOID`) and **then** voided with `POST /v3/company/{realmId}/invoice?operation=void` and the sync token the rename handed back. The rename is what gives the number back, since QuickBooks will not reuse one a voided invoice still holds (decision 34); a rename that fails is logged and the void goes ahead anyway. The replacement invoice is a new create whose `PrivateNote` notes which invoice it replaces.

### Sandbox or production

Tests never touch either: they replay recorded responses. Live work uses the production keys against Icon's own company (decisions 31 and 51), because the sandbox's sample data is nothing like Icon's setup. Switching between the two is a settings change (`QBO_ENVIRONMENT`, the matching keys, `QBO_REDIRECT_URI`) plus `fops qbo-connect`. `fops doctor` fails until the stored connection matches the setting.

## Setting up a new engagement (decision 56)

Only after Kevin has filled in the setup form and confirmed it with the one-time number. `QuickBooksOnline.set_up_engagement` finds each record before it creates it, in dependency order, so a second attempt finishes rather than duplicates:

1. **Term**: an existing term whose `DueDays` matches, else `POST /term` with `Name` `Net N` and `DueDays`.
2. **Customer** (new client only): found by display or company name as invoicing finds it; else `POST /customer` with `DisplayName`, `CompanyName`, `PrimaryEmailAddr`, `SalesTermRef`, and `Notes` `Invoice code: XX`. An existing customer without an invoice code gets one by sparse update; nothing else on it changes.
3. **Category**: an `Item` of `Type` `Category` named as the customer, else `POST /item` with `Name` and `Type: Category`.
4. **Vendor**: by `DisplayName`. An existing vendor with a different email is refused; one with no email gets this one by sparse update; else `POST /vendor` with `DisplayName`, `CompanyName` (the firm), `PrimaryEmailAddr`, `TermRef`.
5. **Product**: by `FullyQualifiedName` `Client:Consultant`. An existing one at a different rate is refused; else `POST /item` with `Type: Service`, `SubItem: true`, `ParentRef` the category, `UnitPrice`, `PurchaseCost`, `PrefVendorRef`, `PurchaseDesc` `Start: YYYY-MM-DD`, and the `IncomeAccountRef` and `ExpenseAccountRef` copied from an existing engagement product (refused when there is none to copy).

Every setup write asks for `minorversion=75` unless `FOPS_QBO_MINORVERSION` already names one: creating a category needs a newer record shape than the default. Contract tests: `tests/contract/test_quickbooks_setup.py`.

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

The first real exercise is `fops qbo-test-invoice` against Icon's own company, with the production keys (decision 51; see the roadmap, PR 16).
