# How it works

This is the whole process in plain words. It is written for Kevin as much as for the people building the agent. The five files in `context/` describe the business itself; this file describes what the agent does with it.

## The short version

1. A consultant emails an approved timesheet to the agent's mailbox.
2. The agent reads it and works out who it is from, which client, which dates, how many approved hours, and whether the client approved it.
3. The agent looks up the engagement list for the bill rate, pay rate, billing contact, billing schedule, and payment terms.
4. The agent emails Kevin the details it found, for his records.
5. If anything is unclear, the agent emails Kevin "needs your review" and waits.
6. If everything checks out, the agent prepares the invoice (approved hours × bill rate) and emails it to the client's billing contact with the timesheet attached and Kevin on CC. Depending on the mode, it asks Kevin first.
7. The agent emails Kevin what the consultant (or vendor company) is owed (approved hours × pay rate) and when it is due. Kevin pays as he does today.
8. The agent updates its tracking sheet. Once QuickBooks Online is connected, it checks daily whether the client has paid.
9. Every Monday the agent emails Kevin a summary.

## Step by step

### 1. A timesheet arrives

Consultants keep sending timesheets exactly as they do now, just to the agent's address (for example `jay@icon-technologies.com`) instead of, or as well as, Kevin's. Attachments can be PDFs, spreadsheets, screenshots, or exports from the client's time system. The email itself might be short ("August timesheet attached") or a forwarded approval from the client's manager.

The agent checks its mailbox every 15 minutes. Emails from addresses it does not know (not a consultant, vendor, client, or Icon address in the engagement list) are set aside in a *Needs Review* folder and mentioned in the Monday summary, but never processed.

### 2. Reading the timesheet

The agent uses an AI model to read the attachment in whatever layout it comes in and pull out:

- the consultant's name,
- the client's name (and the end client, if shown),
- the first and last date the timesheet covers,
- hours per day when shown, and the total,
- whether it was approved, and what shows that (an "Approved" status, an approver name and date, a signature, or a forwarded approval email).

For each of these it also keeps the exact words it relied on and how sure it is. The model only reads; it never does the arithmetic, never picks the rate, and never decides who to email. Those parts are done by ordinary code using the engagement list.

### 3. Matching to the engagement list

The agent finds the consultant (by the sender's email address, or by the name on the timesheet) and the client (from the consultant's active engagements for those dates; if there is more than one, the client name on the timesheet decides). From the engagement row it takes the bill rate, pay rate, billing schedule, payment terms, and billing contact.

The dates on the timesheet must line up with one billing period on that engagement's schedule. If a consultant sends weekly timesheets for an engagement billed monthly, the agent collects them and invoices once the whole month is covered.

### 4. Details for Kevin's records

As soon as a timesheet is read, the agent emails Kevin what it found: consultant, client, period, hours, approval, and the file itself. This happens every time, whether or not anything else needs attention, so Kevin's records stay complete.

### 5. When something is unclear

If the agent cannot be sure about who, which client, which dates, how many hours, whether it was approved, the rate, or who to send the invoice to, it does not guess. It emails Kevin a "needs your review" message that says what it found, what is unclear, and what Kevin can do about it, which is usually one of:

- fix or add a row in the engagement list, or
- reply to the email with the answer, in his own words (for example "this is for Acme", "use 152 hours", "yes, approved by Jane on the phone"), or
- reply "ignore".

Before the email goes, the agent looks into the problem itself, and the email ends with what it found and up to three ways out, lettered A to C; Kevin can reply with just the letter. It looks only; nothing it suggests happens until Kevin chooses, and the agent checks his reply before acting on it. Whatever he replies, he hears back what was done.

The item waits until Kevin answers. Nothing is sent to a client in the meantime. The full list of reasons is in `timesheet-checks.md`.

### 6. The invoice and the billing email

When everything checks out, the agent prepares:

- the invoice: one line, "consultant — role — period", quantity = approved hours, rate = bill rate, total = hours × bill rate, due date from the client's payment terms;
- the billing email to the client's billing contact, with Kevin on CC, the invoice PDF attached, and the consultant's original timesheet file attached unchanged.

What happens next depends on the mode the agent is running in:

| Mode | What the agent does with a clean timesheet |
|---|---|
| **Dry run** | Emails Kevin the invoice and the billing email it *would* send. Sends nothing to clients. Kevin does the rest by hand. This is how the agent starts. |
| **Ask first** | Emails Kevin "Approve this invoice?" with everything attached. When Kevin replies "approve", the agent creates the invoice (in QuickBooks Online once connected; until then it just makes the PDF, and Kevin enters the invoice into QuickBooks himself) and sends the billing email. "Cancel" stops it. |
| **Automatic** | For engagements marked "send automatically" in the engagement list, the agent creates the invoice and sends the billing email without asking. Kevin is still on CC. Anything with a review item or any doubt still goes through "ask first". |

Kevin chooses the mode with one setting. The agent starts in dry run and moves to ask first when Kevin is comfortable. **Icon runs in ask first, in production too** (decision 57): Kevin sees every invoice before the client does. Automatic mode is still in the code but is not used.

### 7. What the consultant is owed

Using the same approved hours, the agent works out approved hours × pay rate and emails Kevin a payment instruction: who to pay (the consultant, or their vendor company), the period, the hours, the pay rate, the amount, when it is due (from the consultant's pay timing), and how they are paid (bank transfer, payroll, or check). Kevin makes the payment as he does today. The agent does not track whether the payment was made and does not send reminders.

### 8. Keeping records

Every timesheet item has a status (see `status-tracking.md`). The agent keeps an Excel tracking sheet up to date with one row per item: consultant, client, period, hours, rates, invoice amount, amount owed, status, dates, invoice number. Once QuickBooks Online is connected, the agent checks once a day whether invoices it created have been paid and updates the status.

### 9. The Monday summary

Every Monday the agent emails Kevin: timesheets received last week, invoices sent, items waiting for his review, engagements with no timesheet yet for the last period, unpaid invoices past their due date (when QuickBooks can say what is paid), emails it set aside, duplicates it filed, and anything that looks stuck.

## What the agent never does

- Never pays anyone or moves money.
- Never takes a rate from a timesheet or from Claude. Rates come from the engagement's product in QuickBooks (or the engagement list where QuickBooks has none). The one way a rate gets into QuickBooks through the agent is Kevin's own setup form, which he confirms with a one-time number.
- Never invoices hours it cannot see were approved.
- Never sends anything to a client without Kevin on CC.
- Never sends the same invoice twice, and never sends an invoice for a consultant and period it has already invoiced unless Kevin tells it to replace one.
- Never guesses. When unsure, it asks Kevin.
- Never touches recruiting or candidates.

## Pictures

Every path the agent can take, as pictures. The letters match the checklist in
`pathways.md`, which says for each path what decides it, what happens, and
which test covers it. Diamonds are decisions; the words on each arrow say what
sends the agent that way. Unless a picture says otherwise, Icon runs in **ask
first** (decision 57).

### 1. One run, every 15 minutes

```mermaid
flowchart TD
    start(["Run starts: one at a time"]) --> mail["Read new mail; if the mailbox can't be read, carry on with what is stored (A6)"]
    mail --> list{"Where do the engagements come from?"}
    list -- "spreadsheet" --> sheet["Read the engagement list, every run"]
    list -- "QuickBooks" --> today{"Copy of QuickBooks taken today?"}
    today -- "yes" --> copy["Use the copy: no QuickBooks calls"]
    today -- "no, or Kevin said try again" --> fresh["Take a fresh copy from QuickBooks"]
    fresh -- "QuickBooks can't be asked" --> stale["Use the last copy, email Kevin once (B3)"]
    sheet --> problems["Bad rows or records become reviews (B1, B2)"]
    copy --> problems
    fresh --> problems
    stale --> problems
    problems --> daily{"First run of the day?"}
    daily -- "yes" --> expect["Every ended period with no timesheet becomes waiting for timesheet (A2)"]
    daily -- "no" --> emails
    expect --> emails["Handle each new email: picture 2"]
    emails --> retry["Ask QuickBooks again about anything waiting on it (F9)"]
    retry --> setaside["Look again at set-aside emails: picture 6"]
    setaside --> ready["Records that are ready: picture 5"]
    ready --> paid{"Paid check done today?"}
    paid -- "no" --> askpaid["Ask QuickBooks which invoices are paid (J6)"]
    paid -- "yes" --> monday
    askpaid --> monday{"Monday?"}
    monday -- "yes" --> summary["Write the Monday summary (J8)"]
    monday -- "no" --> look
    summary --> look["Look into each stuck item before Kevin's email goes (L1)"]
    look --> send["Send everything written down, never twice (J1-J3)"]
    send --> tracking["Rewrite the tracking sheet"]
    tracking --> done(["Run ends"])
```

### 2. What an email is

Decided by code from the sender's address. The email's words are never trusted.

```mermaid
flowchart TD
    e(["A new email"]) --> seen{"Same Message-ID seen before?"}
    seen -- "yes" --> skip["Skip it (C8)"]
    seen -- "no" --> who{"Who sent it?"}
    who -- "Kevin" --> answers{"Answers one of the agent's emails? its reply header, then its subject"}
    answers -- "yes" --> kevin["His reply: picture 5 or 6"]
    answers -- "no" --> nomatch["Nothing done; Kevin told so once, in the same thread (H10)"]
    who -- "a consultant or their vendor" --> ts["A timesheet: picture 3"]
    who -- "a forwarder on the list" --> ts
    who -- "a client address or domain" --> client["Kevin gets it as it came; nothing in it is acted on (C4)"]
    who -- "nobody in the engagements" --> mode{"QuickBooks mode, and no fresh copy yet this run?"}
    mode -- "yes" --> refresh["Take one fresh copy from QuickBooks"]
    refresh --> again{"Known now?"}
    again -- "yes" --> ts
    again -- "no" --> unknown
    mode -- "no" --> unknown{"Has an attachment?"}
    unknown -- "yes" --> asidemail["Set aside; email Kevin with replies and the setup form (C6)"]
    unknown -- "no" --> asidequiet["Set aside; listed in the Monday summary (C7)"]
```

### 3. A timesheet

A check that finds a problem does not stop the others: everything wrong goes on
one list, and Kevin gets one email at the end listing all of it. The early
stops are only the ones that leave nothing to check (no file, a file already
seen, nobody or no engagement to put it on).

```mermaid
flowchart TD
    t(["A timesheet email"]) --> att{"Attachment?"}
    att -- "no" --> r1["Review: NO_ATTACHMENT (D1)"]
    att -- "yes" --> dup{"This exact file seen before?"}
    dup -- "yes" --> quiet["Filed quietly as a duplicate (D3)"]
    dup -- "no" --> read["Claude reads every attachment into the form"]
    read -- "can't read any" --> r2["Review: CANT_READ_ATTACHMENT (D2)"]
    read --> who{"Whose? sender first, then the name on the page"}
    who -- "can't tell" --> miss1{"QuickBooks mode and no fresh copy yet?"}
    miss1 -- "yes" --> fresh1["Take one fresh copy, try again"]
    fresh1 --> who
    miss1 -- "no" --> aside1["Set aside with the setup form (E2)"]
    who -- "known" --> which{"Which engagement? the dates, then the client named"}
    which -- "none, or can't tell" --> aside2["Set aside with the setup form (E3)"]
    which -- "one" --> period{"Fits the billing schedule?"}
    period -- "no" --> r3["Review: PERIOD_MISMATCH or PERIOD_UNCLEAR (E6, E7)"]
    period -- "yes" --> rate{"Rate for these dates?"}
    rate -- "no, or it changes mid-period" --> r4["Review: RATE_MISSING or LIST_ROW_PROBLEM (E10, E11)"]
    rate -- "yes" --> checks["Check hours, approval, Claude's confidence; problems go on the list (F1-F5)"]
    checks --> money["Ask QuickBooks for this engagement's rates and billing details (F7)"]
    money -- "QuickBooks can't answer" --> hold["Kept; invoice waits; retried every run (F9)"]
    money -- "disagrees with the spreadsheet" --> differ["QuickBooks' figure used; Kevin told (F8)"]
    money -- "no billing email anywhere" --> nobill["Kept; invoice waits for an address, then carries on by itself (E12)"]
    money --> same{"Another timesheet for this record and dates?"}
    same -- "same hours and approval" --> quiet2["Filed quietly as a duplicate (K1)"]
    same -- "different" --> corr["Review: CORRECTION (K2, K3)"]
    same -- "no" --> told["Kevin gets Timesheet received for his records, every time"]
    told --> found{"Anything on the list to ask Kevin?"}
    found -- "yes" --> review["One Needs your review email listing everything (F6)"]
    found -- "no" --> covered{"Whole period covered?"}
    covered -- "no" --> wait["Kept; waits for the rest (E8)"]
    covered -- "yes" --> isready["Hours summed, amounts worked out: ready, picture 5"]
```

### 4. What a record goes through

The statuses in `status-tracking.md` and the only ways one turns into another.

```mermaid
stateDiagram-v2
    [*] --> waiting_for_timesheet: period ended, nothing yet
    [*] --> received: timesheet arrived first
    waiting_for_timesheet --> received: timesheet arrives
    received --> needs_review: something to ask Kevin
    received --> ready: all checks pass, period covered
    needs_review --> received: Kevin answers
    needs_review --> ready: answers applied
    needs_review --> ignored: Kevin says ignore
    ready --> waiting_for_approval: ask first, invoice made
    ready --> needs_review: invoice failed, or a correction
    waiting_for_approval --> invoice_sent: Kevin says approve
    waiting_for_approval --> cancelled: Kevin says cancel, invoice voided
    waiting_for_approval --> waiting_for_timesheet: Kevin says wrong client, invoice voided
    waiting_for_approval --> needs_review: sending failed, or a correction
    invoice_sent --> client_paid: QuickBooks shows it paid
    invoice_sent --> needs_review: correction after sending
    client_paid --> [*]
    ignored --> [*]
    cancelled --> [*]
```

### 5. Ready to invoice, and Kevin's answer

```mermaid
flowchart TD
    r(["A record is ready"]) --> m{"Mode"}
    m -- "dry run" --> preview["Kevin gets Dry run - would invoice; nothing made (A5)"]
    m -- "ask first" --> make["Make the invoice in QuickBooks"]
    make -- "QuickBooks refuses" --> qbfail["Review: QUICKBOOKS_FAILED, saying who holds a taken number; nothing to the client (J4)"]
    qbfail --> looked["The review gains what was found and lettered ways out (L1)"]
    make -- "total disagrees" --> voidnow["Voided at once; both totals reported (J5)"]
    make --> approve["Kevin gets Approve? with the real invoice (H1)"]
    approve --> answer{"Kevin's reply, first words, read by code"}
    answer -- "approve" --> bill["Billing email to the client, Kevin on CC (H2)"]
    bill --> pay["Payment instruction to Kevin"]
    pay --> paidcheck["Daily paid check: client paid (J6)"]
    answer -- "cancel" --> cancel["Invoice voided; record cancelled (H3)"]
    answer -- "wrong client" --> wrong["Invoice voided; first client waits for its own timesheet; timesheet set aside with the setup form (H4)"]
    wrong --> six["Picture 6, then back to picture 3 for the right client"]
    answer -- "anything else" --> plain{"Claude reads it; code checks the words are his"}
    plain -- "approves, and nothing else" --> bill
    plain -- "cancels" --> cancel
    plain -- "asks for a change, or unclear" --> askwords["Reply: it can go as it is, be cancelled, or be the wrong client (H6)"]
```

### 6. A set-aside email, and setting something up in QuickBooks

```mermaid
flowchart TD
    s(["Set aside: unknown address, someone or some engagement it can't place, or wrong client"]) --> reply{"Kevin's reply"}
    reply -- "nothing yet" --> auto{"Address shows up in the next day's copy?"}
    auto -- "yes" --> handle["Handled again: picture 3"]
    auto -- "no" --> stays["Stays in Needs Review and the Monday summary"]
    reply -- "try again" --> look["Fresh copy, look again"]
    look -- "placed now" --> handle
    look -- "still not" --> stuck["Still needs your review (H9)"]
    reply -- "this is from Priya Shah" --> named{"One consultant by that name?"}
    named -- "yes" --> handle
    named -- "no" --> stuck
    reply -- "ignore" --> closed["Closed; stays in Needs Review"]
    reply -- "the setup form, filled in" --> form{"Read by code: anything blank or wrong?"}
    form -- "yes" --> fix["Problems named, his answers shown back (I2)"]
    form -- "no" --> confirm["Set up in QuickBooks? everything listed, with a one-time number (I1)"]
    confirm --> c{"Kevin's reply"}
    c -- "cancel" --> nochange["Nothing made (I5)"]
    c -- "confirm without the right number" --> nochange2["Nothing made; told so (I4)"]
    c -- "confirm and the number" --> drymode{"Dry run?"}
    drymode -- "yes" --> wouldset["Dry run - would set up; nothing made (I9)"]
    drymode -- "no" --> create["Find or make: term, customer, category, vendor, product with both rates (I3)"]
    create -- "QuickBooks refuses" --> refused["Couldn't finish setting up; retried every run (I8)"]
    create -- "done" --> madeit["Set up in QuickBooks: what was made and reused"]
    madeit --> handle
```

### 7. When something outside the agent fails

```mermaid
flowchart TD
    f(["Something outside fails"]) --> which{"What?"}
    which -- "QuickBooks, for the day's copy" --> f1["Last copy used; Kevin told once; closes itself (B3, B4)"]
    which -- "QuickBooks, for one engagement's rates" --> f2["Invoice waits; retried every run (F9)"]
    which -- "QuickBooks, making the invoice" --> f3["Review; timesheet back in Needs Review (J4)"]
    which -- "QuickBooks, the paid check" --> f4["Review; asked again next run (J7)"]
    which -- "QuickBooks, setting something up" --> f5["Kevin told once per reason; retried (I8)"]
    which -- "sending an email" --> f6["Retried; then SEND_FAILED or SEND_UNCERTAIN (J1-J3)"]
    which -- "the mailbox, or Claude" --> f7{"Lasted an hour, or a refused password or key?"}
    f7 -- "no" --> f8["Waits; picked up on a later run; nobody bothered (A6, D5)"]
    f7 -- "yes" --> f9["Kevin told once; told again when it works (A6, D5)"]
    which -- "filing an email in a folder" --> f10["Logged; the email is still handled once (A7)"]
```
