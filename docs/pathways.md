# Every path the agent can take

A checklist of every way a run can go, what decides which way it goes, and
what happens at the end of it. It is for testing: the automated tests already
walk each path on fakes, and **the boxes here are for trying each one for
real** -- with the real mailbox, real QuickBooks (the sandbox first), and Kevin
reading the emails. Tick a box when the path has been seen working end to end
and the outcome was what this page says. The pictures of the same paths are in
`how-it-works.md`.

How to read an entry:

- **When** -- the situation that sends the agent down this path.
- **Decided by** -- what the agent looks at to choose it. "Code" means ordinary
  rules; "Claude" means the model's reading of a document or reply.
- **Outcome** -- what the agent does and what Kevin (or anyone else) sees.
- **Test** -- the automated test that covers it, or **none yet**.

Every path assumes **ask first** mode unless it says otherwise: that is how Icon
runs (decision 57). Paths marked *QuickBooks mode* need
`FOPS_ENGAGEMENTS=quickbooks`.

When a change adds a path, add it here in the same pull request.

---

## A. Every run

- [ ] **A1. A quiet run.**
  When: nothing new in the mailbox, the day's first run has already happened.
  Decided by: code -- no new mail, nothing waiting, today's look already done.
  Outcome: QuickBooks is asked nothing; the tracking sheet is rewritten; nothing is emailed.
  Test: `test_quiet_runs.py::TestAQuietRun`.
- [ ] **A2. The day's first run.**
  When: first run after midnight.
  Decided by: code -- the date the last look was done.
  Outcome: a fresh copy of the engagements is taken from QuickBooks (QuickBooks mode); every billing period that has ended with no timesheet becomes *waiting for timesheet*.
  Test: `test_quiet_runs.py::TestTheDailyLook`, `test_engagement_copy.py::TestTheCopy`.
- [ ] **A3. Two runs at once.**
  When: a run is still going when the next one starts.
  Decided by: code -- the run lock.
  Outcome: the second run says so and stops; nothing is done twice.
  Test: `tests/unit/test_lockfile.py`.
- [ ] **A4. Stopped half-way.**
  When: the machine restarts or the run is killed mid-run.
  Decided by: code -- each email is stored before it is handled and marked done after; every outgoing email and invoice is written down first.
  Outcome: the next run finishes exactly what was left; nothing is sent or created twice.
  Test: `test_run.py::TestNeverTwice`, `test_emails_and_invoices.py::TestNeverTwice`.
- [ ] **A5. Dry run.**
  When: `FOPS_MODE=dry_run`.
  Decided by: the setting; no command-line flag can override it.
  Outcome: everything is read and checked; Kevin gets "Dry run — would invoice" previews; nothing goes to a client, nothing is created in QuickBooks.
  Test: `test_emails_and_invoices.py::TestDryRun`.

## B. The engagements (who works where, at what rate)

- [ ] **B1. The spreadsheet has a bad row.**
  Decided by: code checking each row.
  Outcome: one *Needs your review* email naming the sheet and row; that engagement is left out until fixed. Read every run, so a fix is noticed within 15 minutes.
  Test: `test_run.py` (list problems), `test_quiet_runs.py::TestAQuietRun::test_still_catches_a_broken_spreadsheet_edit`.
- [ ] **B2. A QuickBooks record is incomplete** *(QuickBooks mode)* -- e.g. no `Invoice code:` on a customer, no `Start:` on a product.
  Outcome: a review naming the record and what to fill in; that engagement is left out.
  Test: `test_engagements_from_quickbooks.py::test_something_missing_in_quickbooks_is_a_review_naming_the_record`.
- [ ] **B3. QuickBooks can't be reached for the day's copy** *(QuickBooks mode)*.
  Outcome: the run carries on from yesterday's copy (or the spreadsheet if there is none); Kevin gets one email saying so; it retries every run and closes the question by itself when QuickBooks answers. Replies: "try again", "ignore".
  Test: `test_engagement_copy.py::TestQuickBooksCannotBeAskedForTheCopy`.
- [ ] **B4. QuickBooks needs reconnecting.**
  Outcome: as B3, but the email says to run `fops qbo-connect`.
  Test: `test_engagement_copy.py::TestQuickBooksCannotBeAskedForTheCopy::test_a_dead_connection_says_how_to_reconnect`.
- [ ] **B5. A product made inactive in QuickBooks** (engagement finished).
  Outcome: no new periods expected for it; work already in hand carries on; a line in the run report, not a review.
  Test: `test_live_engagements_run.py::TestQuickBooksSaysAnEngagementHasFinished`.
- [ ] **B6. QuickBooks has an engagement the spreadsheet has no row for** *(spreadsheet mode)*.
  Outcome: a review once, asking Kevin to add the row or make the product inactive.
  Test: `test_live_engagements_run.py::TestQuickBooksHasAnEngagementTheListCannotSchedule`.
- [ ] **B7. A consultant or client renamed in QuickBooks.**
  Decided by: code -- QuickBooks' own id for the engagement.
  Outcome: the same record carries on under the new name; no second invoice.
  Test: `test_emails_and_invoices.py::TestARenamedEngagement`.

## C. What an email is

Decided by code from the sender's address, never by reading the email.

- [ ] **C1. From a consultant** (their address, or their vendor's in QuickBooks).
  Outcome: handled as a timesheet (section D).
  Test: `test_run.py::TestHappyPath`.
- [ ] **C2. From an address on the forwarders list** (`FOPS_TIMESHEET_FORWARDERS`).
  Outcome: handled as a timesheet; whose it is comes off the document.
  Test: `test_run.py::TestForwardedTimesheets`.
- [ ] **C3. From Kevin.**
  Outcome: handled as his reply (section H).
  Test: `test_emails_and_invoices.py::TestAskFirst`, `TestReviewReplies`.
- [ ] **C4. From a client** (a billing or CC address, or the client's email domain).
  Outcome: filed as processed. *See gap G3: Kevin is not told.*
  Test: none yet.
- [ ] **C5. From an address not in the copy** *(QuickBooks mode)*.
  Decided by: code -- the address is missing, so one fresh copy is taken from QuickBooks.
  Outcome: if the fresh copy has the address (a new consultant added today), it is handled as a timesheet.
  Test: `test_engagement_copy.py::TestAnAddressNotInTheCopy::test_a_fresh_copy_is_taken_and_the_timesheet_handled`.
- [ ] **C6. Still unknown, with an attachment.**
  Outcome: set aside in Needs Review; Kevin gets *Needs your review* with replies "try again", "this is from Priya Shah", "ignore", and (QuickBooks mode) the setup form (section I).
  Test: `test_engagement_copy.py::TestAnAddressNotInTheCopy::test_still_unknown_is_set_aside_and_kevin_is_told_what_he_can_do`.
- [ ] **C7. Still unknown, no attachment** (a newsletter).
  Outcome: set aside; no email of its own; listed under "set aside" in the Monday summary.
  Test: `test_engagement_copy.py::TestAnAddressNotInTheCopy::test_without_an_attachment_it_waits_for_the_monday_summary`.
- [ ] **C8. The same email delivered twice.**
  Decided by: code -- its Message-ID.
  Outcome: stored once, handled once.
  Test: `test_run.py::TestDuplicates::test_same_email_arriving_again_is_skipped`.

## D. A timesheet: getting it read

- [ ] **D1. No attachment.** Outcome: `NO_ATTACHMENT` review. Test: `test_run.py::TestReviews::test_no_attachment`.
- [ ] **D2. An attachment Claude can't read** (a refusal, a corrupt file). Outcome: `CANT_READ_ATTACHMENT` review with the file attached. Test: `test_run.py::TestReviews::test_unreadable_attachment`.
- [ ] **D3. The same file sent again** (or forwarded by someone else). Decided by: code -- the file's fingerprint. Outcome: filed quietly in Ignored; no email. Test: `test_run.py::TestDuplicates`.
- [ ] **D4. A timesheet and the firm's invoice in one email.** Decided by: code -- which reading shows approval. Outcome: both read; the timesheet is the one kept, shown to Kevin and sent to the client. Test: `test_emails_and_invoices.py::TestWhichFileIsAttached`.
- [ ] **D5. Claude can't be reached at all** (API down). Outcome: the run stops; the email stays unhandled and is read on the next run. *See gap G2.* Test: none yet.

## E. A timesheet: placing it

- [ ] **E1. Clean: one consultant, one engagement covering the dates.** Outcome: *Timesheet received* email; record becomes *ready*. Test: `test_run.py::TestHappyPath`.
- [ ] **E2. The consultant can't be told** (no name match, or two). Decided by: code -- sender, then the name on the page against names and other names. Outcome *(QuickBooks mode)*: one fresh copy is taken first; if still unknown, set aside with "try again", "this is from …", the setup form, "ignore". Test: `test_engagement_copy.py::TestATimesheetThatCannotBePlaced`.
- [ ] **E3. No engagement covers the dates, or two do and the page names neither.** Outcome: as E2, `ENGAGEMENT_UNCLEAR`. Test: `test_setting_up.py::TestATimesheetForANewClient`.
- [ ] **E4. Two engagements cover the dates; the page names one.** Decided by: code -- client name, legal name, names on timesheets. Outcome: that engagement. Test: `test_live_engagements_run.py::test_the_client_is_matched_under_any_of_its_names`.
- [ ] **E5. One engagement covers the dates but the page names another client** (a consultant starting at a second client). Decided by: code -- dates win. Outcome: put on the first client; Kevin catches it at approval with "wrong client" (H4). Test: `test_wrong_client.py`.
- [ ] **E6. Dates don't fit the billing schedule.** Outcome: `PERIOD_MISMATCH` review. Test: `test_run.py::TestReviews::test_dates_not_matching_the_schedule`.
- [ ] **E7. Dates can't be read.** Outcome: `PERIOD_UNCLEAR` review. Test: `test_straddling_months.py`.
- [ ] **E8. Part of a monthly period** (weekly timesheets). Outcome: kept; waits for the rest; one invoice when the month is covered. Test: `test_run.py::TestWeeklyIntoMonthly`.
- [ ] **E9. A week straddling two months.** Decided by: code -- the days or a note on the page saying how many hours belong to the month. Outcome: only the month's own hours billed, or `PART_WEEK_UNCLEAR` / `PART_WEEK_DISAGREES` review. Test: `test_straddling_months.py`.
- [ ] **E10. The rate changes in the middle of the period.** Outcome: review naming both rows; never a split invoice. Test: `test_run.py::TestRateChangeMidPeriod`.
- [ ] **E11. No rate for these dates.** Outcome: `RATE_MISSING` review. Test: `tests/unit/test_checks.py` only.
- [ ] **E12. The client has no billing email.** Outcome: `NO_BILLING_CONTACT` review. Test: **none yet** (gap G6).

## F. A timesheet: hours, approval, confidence, money

- [ ] **F1. Daily hours don't add up to the total.** Outcome: `HOURS_DONT_ADD_UP`. Test: `test_run.py::TestReviews::test_daily_hours_not_adding_up`.
- [ ] **F2. No hours found.** Outcome: `HOURS_MISSING`. Test: `tests/unit/test_checks.py`.
- [ ] **F3. Unusual hours** (over 24 in a day, over 25% above full time, or zero). Outcome: `HOURS_UNUSUAL`. Test: `tests/unit/test_checks.py`.
- [ ] **F4. No approval visible.** Outcome: `NO_APPROVAL`. Test: `test_run.py::TestReviews::test_no_approval`.
- [ ] **F5. Claude unsure about a field.** Decided by: Claude's confidence. Outcome: `NOT_SURE` review naming the field. Test: `tests/unit/test_checks.py`.
- [ ] **F6. Several problems at once.** Outcome: one email listing all of them. Test: `test_run.py::TestReviews::test_one_review_email_lists_everything`.
- [ ] **F7. Rates taken when the timesheet is read** (including a record made earlier when its period ended). Decided by: code -- the product in QuickBooks. Outcome: today's rates used. Test: `test_engagement_copy.py::TestRatesForTheEngagementInHand::test_a_waiting_item_takes_the_rate_quickbooks_has_now`.
- [ ] **F8. QuickBooks and the spreadsheet disagree** on a rate, payee, billing address or terms. Outcome: QuickBooks' figure used; the record waits; Kevin told. Test: `test_emails_and_invoices.py::TestWhatTheClientIsCharged`, `TestWhatIconPays`, `TestContactsAndTermsFromQuickBooks`.
- [ ] **F9. QuickBooks can't confirm the rates for the timesheet in hand.** Outcome: the timesheet is kept; the invoice waits; Kevin told; every run asks again and it carries on by itself; "ignore" drops it. Test: `test_engagement_copy.py::TestRatesForTheEngagementInHand`.
- [ ] **F10. No invoice code for the client, or the consultant's initials can't be worked out.** Outcome: review rather than a guessed invoice number. Test: `test_emails_and_invoices.py::TestInvoiceNumbering`.

## G. Duplicates and corrections

- [ ] **G1. A different file with the same dates, hours and approval.** Outcome: filed quietly as a duplicate. Test: `test_run.py::TestDuplicates`.
- [ ] **G2. A corrected timesheet before the invoice went.** Outcome: `CORRECTION` review; "use the new one" replaces it. Test: `test_run.py::TestCorrections::test_corrected_before_sent`.
- [ ] **G3. A corrected timesheet after the invoice went.** Outcome: `CORRECTION` review; "use the new one" voids and replaces the invoice under the next number. Test: `test_run.py::TestCorrections::test_corrected_after_sent`, `test_emails_and_invoices.py::TestInvoiceNumbering`.

## H. The invoice, and Kevin's replies

- [ ] **H1. Ready, ask first.** Outcome: the invoice is made in QuickBooks (so Kevin approves the real one), and Kevin gets *Approve? Invoice for …* with the PDF. Test: `test_emails_and_invoices.py::TestAskFirst`.
- [ ] **H2. "approve".** Decided by: code -- the first word. Outcome: billing email to the client with Kevin on CC; then the payment instruction to Kevin. Test: `test_emails_and_invoices.py::TestAskFirst::test_kevin_is_asked_and_approving_sends_the_invoice`.
- [ ] **H3. "cancel".** Outcome: the invoice is renamed `-VOID` and voided; the record is cancelled; nothing goes out. A void QuickBooks refuses is a review telling Kevin to void it by hand. Test: `test_emails_and_invoices.py::TestAskFirst`.
- [ ] **H4. "wrong client".** Outcome: the draft voided; the first client's period goes back to waiting for its own timesheet; the timesheet is set aside with the setup form and never put back on that client; once the right client is set up (form, or by hand + "try again") a new approval comes for it. Test: `test_wrong_client.py`.
- [ ] **H5. "wrong client" after approving.** Outcome: nothing changes; Kevin told it has gone and needs correcting as a correction. Test: `test_wrong_client.py::TestKevinSaysWrongClient::test_after_approval_it_is_too_late_and_nothing_changes`.
- [ ] **H6. Anything else to the approval email.** Outcome: a short reply listing the three words that work. Test: `test_emails_and_invoices.py::TestAskFirst::test_anything_else_gets_a_short_reply_asking_for_one_of_the_two_words`.
- [ ] **H7. A reply to a review** ("use 152 hours", "this is for Acme", "approved by Jane on 9/3", "use the new one", "ignore"). Decided by: Claude reads the reply into typed answers; code applies them. Outcome: checks re-run; ready, or asked again. Test: `test_emails_and_invoices.py::TestReviewReplies`.
- [ ] **H8. A reply Claude can't make sense of.** Outcome: "Sorry to ask again", with the shapes that work. Test: `test_emails_and_invoices.py::TestReviewReplies::test_an_unclear_reply_makes_the_agent_ask_again`.
- [ ] **H9. Replies about a set-aside email** -- "try again", "this is from Priya Shah", "ignore", or unclear. Outcome: handled again / handled as hers / closed / asked again; "try again" with nothing new gets *Still needs your review*. An address fixed in QuickBooks without a reply is picked up the next day. Test: `test_engagement_copy.py::TestKevinAnswersAboutAnUnknownAddress`.
- [ ] **H10. A reply from Kevin that matches no email of the agent's.** Outcome: a line in the run report only. *See gap G4.* Test: none yet.
- [ ] **H11. A reply to a review that names an answer not asked about.** Outcome: applied to the closest open question for that record, or asked again. Test: `test_emails_and_invoices.py::TestReviewReplies`.

## I. Setting something up in QuickBooks *(QuickBooks mode)*

- [ ] **I1. Kevin fills in the form.** Decided by: code only (never Claude) -- labelled lines, his own part of the reply. Outcome: *Set up in QuickBooks? …* listing what will be made or reused, both rates, and a one-time number. Nothing made yet. Test: `test_setting_up.py::TestKevinFillsItIn`.
- [ ] **I2. The form has problems** (blank, bad rate/date/email, invoice code taken, engagement already exists). Outcome: each problem named, his answers shown back. Test: `test_setting_up.py::TestKevinFillsItIn::test_problems_are_named_and_his_answers_shown_back`, `tests/unit/test_setup.py`.
- [ ] **I3. "confirm" with the right number.** Outcome: written down, then made in QuickBooks (term, customer, category, vendor, product -- each found before it is made); *Set up in QuickBooks: …* says what was made and reused; the email that started it is handled. Test: `test_setting_up.py::TestKevinConfirms`, `tests/contract/test_quickbooks_setup.py`.
- [ ] **I4. "confirm" with a wrong or missing number.** Outcome: nothing made; told so. Test: `test_setting_up.py::TestKevinConfirms`.
- [ ] **I5. "cancel".** Outcome: nothing made; the original question stays open. Test: `test_setting_up.py::TestKevinConfirms::test_cancel_leaves_quickbooks_alone_and_the_question_open`.
- [ ] **I6. A new client.** Outcome: customer with billing email, terms and `Invoice code:`; category named for it. Test: `test_setting_up.py::TestANewClient`.
- [ ] **I7. An existing consultant at a new client.** Outcome: a second product, under the new category, paid to the vendor she already has. Test: `test_quickbooks_setup.py::TestWhatIsMadeIsReadBackAsToday::test_an_existing_consultant_at_a_new_client_keeps_one_vendor`.
- [ ] **I8. QuickBooks refuses part-way** (duplicate name, a vendor with another email, a product at another rate, no accounts to copy). Outcome: *Couldn't finish setting up*, once per reason; what was made stays; retried every run; nothing existing changed. Test: `test_setting_up.py::TestWhenItCannotBeDone`, `test_quickbooks_setup.py::TestWhatItWillNotDo`.
- [ ] **I9. In dry run.** Outcome: *Dry run — would set up*; nothing made. Test: `test_setting_up.py::TestWhenItCannotBeDone::test_dry_run_says_what_it_would_have_made_and_makes_nothing`.
- [ ] **I10. What was made is read back like a hand-made setup.** Outcome: `fops doctor` passes **quickbooks setup**; the next invoice is priced off the new product. Test: `test_quickbooks_setup.py::TestWhatIsMadeIsReadBackAsToday`.

## J. Sending, and after the invoice

- [ ] **J1. An email that fails to send.** Outcome: retried; after repeated failures a `SEND_FAILED` review. Test: `test_emails_and_invoices.py::TestNeverTwice::test_a_send_that_keeps_failing_becomes_a_review`.
- [ ] **J2. The mail server refuses an address.** Outcome: that email stops; Kevin asked. Test: `test_send_refusals.py`.
- [ ] **J3. Sent, but no proof it left** (crash mid-send). Decided by: code -- the Sent folder. Outcome: found in Sent → done; otherwise `SEND_UNCERTAIN`: "received" or "resend". Test: `test_emails_and_invoices.py::TestNeverTwice`.
- [ ] **J4. QuickBooks refuses the invoice** (e.g. duplicate number, account missing). Outcome: `QUICKBOOKS_FAILED`; the timesheet email goes back to Needs Review; nothing reaches the client. Test: `test_emails_and_invoices.py::TestWhenQuickBooksIsUnhappy`.
- [ ] **J5. The total QuickBooks makes disagrees with the agent's.** Outcome: voided immediately, both totals reported. Test: `tests/contract/test_quickbooks.py::TestCreateInvoice`.
- [ ] **J6. The client pays.** Decided by: the once-a-day paid check (balance zero). Outcome: the record becomes *client paid*; a partial payment changes nothing. Test: `test_paid_check.py`.
- [ ] **J7. The paid check can't reach QuickBooks.** Outcome: a review; the day is not counted as checked, so the next run asks again. Test: `test_paid_check.py::TestWhenQuickBooksCannotAnswer`.
- [ ] **J8. Monday.** Outcome: one summary with the tracking sheet: timesheets received, invoices sent, waiting for review, waiting for approval, no timesheet yet, set aside. Test: `test_emails_and_invoices.py::TestMondaySummary`.
- [ ] **J9. The QuickBooks connection getting old** (80+ days). Outcome: a warning at the end of every run. Test: `tests/contract/test_quickbooks.py::TestTokenStore`.

## Gaps found while writing this list

Paths where the agent does less than the other documents say, or nothing at all. Each needs deciding, not just testing.

- **G1. A mailbox that can't be read.** `MAILBOX_PROBLEM` is a review reason on paper, but nothing raises it: the run stops with an error, and nothing tells Kevin. The heartbeat that would notice the agent has stopped (`fops serve`, roadmap PR 14) is not built yet.
- **G2. Claude unreachable.** Same as G1: the run stops and retries next time, which is right, but nobody is told if it goes on for hours.
- **G3. Client replies.** `emails.md` says they are listed in the Monday summary; they are filed and nothing else. Kevin never hears that a client wrote.
- **G4. A reply from Kevin the agent can't match** to anything it sent goes into the run report only, which Kevin never sees.
- **G5. The Monday summary** does not yet list duplicates filed or unpaid invoices past due, which `emails.md` says it does.
- **G6. `NO_BILLING_CONTACT`** has no whole-run test.
- **G7. A replied-to review with no record behind it** (e.g. a spreadsheet problem): Kevin's answer is recorded but changes nothing; fixing the spreadsheet or QuickBooks is what fixes it.
