# Setting up QuickBooks Online

This is everything that has to be set up in QuickBooks Online for the agent to
bill correctly, and what goes wrong when it is not. All of it is done in the
QuickBooks web app; nothing here needs a developer.

Two things the agent never does, which is why this page exists: **it never
creates a customer and it never creates a product.** If one is missing it stops
and says so rather than inventing something to invoice against.

After any change here, run `fops doctor`. It checks every item below except the
first and the last, and names the client, consultant or setting that is wrong. Each section
ends with the line doctor prints when that part is not right, so a failure can
be read straight back to the thing to fix.

---

## 1. Turn on custom invoice numbers

**Settings (gear) → Account and settings → Sales → Sales form content →
Custom transaction numbers → on.**

The agent numbers invoices in your format: the billing period end date, the
client's two letters, then the consultant's initials — `093025MT-SD`. With this
setting off, QuickBooks ignores the number the agent sends and uses its own
counter, and your numbering is gone.

It also has to be on for a voided invoice to be renamed `…-VOID`, which is what
frees the real number for the invoice that replaces it.

**If it is off:** doctor cannot see this setting, so nothing fails until the
first invoice is made. Then the agent makes the invoice, sees the wrong number,
**voids it immediately**, sends nothing to the client, and tells you:

> I asked QuickBooks to number this invoice 093025MT-SD and it used 1037
> instead. Turn on Settings -> Account and settings -> Sales -> Custom
> transaction numbers, and I'll number invoices the way you do. I voided it and
> sent nothing to the client.

Nothing reaches a client and nothing is billed wrongly — but the number is
spent, so turn the setting on before the first real run rather than after.

---

## 2. Customers

Each client needs a customer in QuickBooks whose name matches the name on the
engagement list.

The agent looks at **Display name** first, then at **Company name**. Either one
matching is enough. So where the display name is a person — the owner, or
whoever was typed in first — put the organisation's name in the Company name
field and leave the display name alone.

Two customers whose company name is the same client is an error, not a
coin-flip: the agent refuses to guess which one to invoice.

**If it is wrong,** `fops doctor` fails the customers check, and says which
company it looked in — worth reading, because a name that is missing from the
sandbox while it sits in the real company looks exactly like a name that is
spelled wrong:

```
FAIL quickbooks customers: I looked in the production company 9130357849073846
and could not use 1 of 3 client name(s). MasTec Inc: QuickBooks has no customer
whose name or company is 'MasTec Inc'. Add it in QuickBooks, or fix the
"QuickBooks customer" column in the engagement list. I never create customers
myself.
```

and, for the other case:

```
QuickBooks has more than one customer whose company is 'MasTec Inc': MasTec
(Atlanta), MasTec Inc. I will not guess which one to invoice. Put the one you
mean in the "QuickBooks customer" column of the engagement list, spelled as
QuickBooks shows it in the list of customers.
```

---

## 3. One product per engagement

A consultant working for two clients needs **two products**, one per client.
That is what lets the two engagements have two different rates — one product
cannot hold both.

QuickBooks allows two products with the same name as long as they are in
different categories, which is why the client's name goes in the category.

| Field | What to put | What the agent does with it |
|---|---|---|
| **Name** | The consultant's name | Prints it in the Description column of the invoice |
| **Category** | The client's name | How it tells one of that consultant's engagements from the other |
| **Sales price/rate** | The **bill rate** — what the client pays per hour | The rate and the amount on the invoice |
| **I purchase this product/service from a vendor** | ticked | Turns on the two fields below |
| **Vendor** | Who Icon pays — the consultant or their company | Who your payment instruction names |
| **Cost** | The **pay rate** — what Icon pays per hour | The amount in your payment instruction |

Both rates come from here and nowhere else. The engagement list still holds its
own copies, and they are now a cross-check rather than the source: where the two
disagree, QuickBooks' figure is the one used, the timesheet waits for you, and
you get a review email naming both numbers. Nothing is invoiced and no payment
is instructed until you answer, so a rate that has drifted costs you a reply
rather than a voided invoice.

A purchase side left blank is not a disagreement — it is one not filled in yet,
and the engagement list is used until it is.

**If the product is missing, ambiguous, or has no rate,** doctor fails the
products check:

```
FAIL quickbooks products: I looked in the production company 9130357849073846
and cannot price 1 of 4 engagement(s). Sridhar Doraiswamy: QuickBooks has no
product for 'Sridhar Doraiswamy'. I looked for MasTec:Sridhar Doraiswamy or
MasTec Inc:Sridhar Doraiswamy, and for a product called 'Sridhar Doraiswamy' on
its own. Every engagement needs a product, under a category named for the
client, with the rate on it. I never create products myself.
```

```
QuickBooks has more than one product called 'Sridhar Doraiswamy' and none of
them is under a category I recognise: Services:Sridhar Doraiswamy, Sridhar
Doraiswamy. I looked for MasTec:Sridhar Doraiswamy. Put the product under a
category named for the client, so I can tell which rate to bill.
```

```
The QuickBooks product for 'Sridhar Doraiswamy' has no rate on it, and the rate
on the product is what I bill. Put their hourly rate on the product in
QuickBooks.
```

**If the bill rate disagrees with the engagement list,** the same check fails
with both numbers, because this one would be found at invoice time otherwise —
by making the invoice and voiding it:

```
Sridhar Doraiswamy at MasTec: MasTec:Sridhar Doraiswamy bills $95.00 an hour and
the engagement list says $92.50.
```

**If the pay rate or the vendor disagrees,** the pay rates check fails
separately. Nothing is paid out of QuickBooks, so no payment instruction is
wrong today, but an item stops and waits for you when it happens during a run:

```
FAIL quickbooks pay rates: QuickBooks and the engagement list do not agree about
what Icon pays. Sridhar Doraiswamy at MasTec: QuickBooks pays $70.00 an hour and
the engagement list says $68.00.
```

When no purchase side is filled in at all, that check says so rather than
passing quietly:

```
ok   quickbooks pay rates: nothing to compare yet: none of 4 engagement(s) has a
rate or a vendor on the purchase side of its product
```

---

## 4. Ending an engagement

**Make the product inactive.** That is the switch: the agent asks QuickBooks
which products are live and stops expecting timesheets for the ones that are
not. You do not have to edit the engagement list to stop it.

Ending an engagement means "expect no more timesheets" and nothing else. It
erases nothing already invoiced, it does not touch an invoice waiting to be
sent, and a late timesheet for a period that already happened is still handled.

Leave the row on the engagement list where it is. The agent still reads the
billing schedule and the start date off it, and QuickBooks holds neither — so a
product with no row behind it is an engagement the agent cannot put a date to,
and it asks you rather than guessing:

```
FAIL quickbooks engagements: 1 engagement(s) in the production company
9130357849073846 have no row on the engagement list, so I cannot tell how often
to expect a timesheet or when the period ends: Sam Okafor at MasTec.
```

The other way round is reported too, because it is usually a mistake rather
than an ending — a product made inactive by accident, or a category renamed:

```
FAIL quickbooks engagements: The engagement list still calls 1 engagement(s)
active and the production company 9130357849073846 has no live product for
them, so I will expect no new periods: Sam Okafor at MasTec. Make the product
active again, or mark the row inactive if the engagement has finished.
```

Marking the row inactive as well is what settles that one — it is not required,
but it is what stops the agent mentioning it every time you run doctor.

**A company with no categories at all** is read as "not set up yet", not as
"every engagement has ended", so an empty QuickBooks never silently stops the
billing:

```
ok   quickbooks engagements: the production company 9130357849073846 lists no
products under a category, so the engagement list decides which engagements are
live, as it did before
```

---

## 5. The invoice template

Each invoice line should show: **period ending date, description, hours, rate,
amount**.

Two of those are QuickBooks fields with your labels on them:

- **Period ending date** is QuickBooks' Service Date field, relabelled. The
  agent sets it to the last day of the billing period, not the invoice date.
- **Description** is the product name, which is the consultant's name.

**If it is wrong:** nothing fails — the invoice just comes out looking wrong.
Worth looking at one real PDF after any edit to the template.

---

## When something here changes

The checks in `fops doctor` and this page are meant to say the same thing: one
for the agent, one for you. When what the agent expects changes, this page
changes in the same pull request (`CLAUDE.md`, definition of done).

The developer-facing half of this is `integrations/quickbooks-online.md`: what
the agent sends to QuickBooks and what it does with the answer. This page says
what to click; that one says what goes over the wire.
