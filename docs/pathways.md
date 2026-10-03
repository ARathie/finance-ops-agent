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
- [ ] **A6. The mailbox can't be read.**
  Decided by: code -- how long since it last answered, and whether it is a refused password.
  Outcome: the run carries on with what is already stored; new mail is fetched once the mailbox answers (nothing lost). Kevin is told once after an hour, at once for a refused password, and again when it works; "ignore" closes the question.
  Test: `test_outages.py::TestTheMailboxCannotBeRead`.
- [ ] **A7. Filing an email in a folder fails.**
  Outcome: logged; the email is still handled once and never again.
  Test: `test_outages.py::TestFilingFails`.
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
  Outcome: Kevin gets *From a client: …* with the text and attachments as they came; the agent acts on nothing in it; filed as processed. Sent once.
  Test: `test_outages.py::TestAClientWrites`.
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
- [ ] **D5. Claude can't be reached at all** (down, overloaded, key refused). Decided by: code -- how long since it last answered, and whether the failure can clear by itself. Outcome: this email and the ones after it wait and are read on a later run; Kevin is told once after an hour (at once for a refused key), and again when it works. Test: `test_outages.py::TestClaudeCannotBeReached`.

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

## K. Duplicates and corrections

(Lettered K so its labels never clash with the gaps at the end, G1–G7.)

- [ ] **K1. A different file with the same dates, hours and approval.** Outcome: filed quietly as a duplicate. Test: `test_run.py::TestDuplicates`.
- [ ] **K2. A corrected timesheet before the invoice went.** Outcome: `CORRECTION` review; "use the new one" replaces it. Test: `test_run.py::TestCorrections::test_corrected_before_sent`.
- [ ] **K3. A corrected timesheet after the invoice went.** Outcome: `CORRECTION` review; "use the new one" voids and replaces the invoice under the next number. Test: `test_run.py::TestCorrections::test_corrected_after_sent`, `test_emails_and_invoices.py::TestInvoiceNumbering`.

## H. The invoice, and Kevin's replies

- [ ] **H1. Ready, ask first.** Outcome: the invoice is made in QuickBooks (so Kevin approves the real one), and Kevin gets *Approve? Invoice for …* with the PDF. Test: `test_emails_and_invoices.py::TestAskFirst`.
- [ ] **H2. "approve".** Decided by: code -- the first word. Outcome: billing email to the client with Kevin on CC; then the payment instruction to Kevin. Test: `test_emails_and_invoices.py::TestAskFirst::test_kevin_is_asked_and_approving_sends_the_invoice`.
- [ ] **H3. "cancel".** Outcome: the invoice is renamed `-VOID` and voided; the record is cancelled; nothing goes out. A void QuickBooks refuses is a review telling Kevin to void it by hand. Test: `test_emails_and_invoices.py::TestAskFirst`.
- [ ] **H4. "wrong client".** Outcome: the draft voided; the first client's period goes back to waiting for its own timesheet; the timesheet is set aside with the setup form and never put back on that client; once the right client is set up (form, or by hand + "try again") a new approval comes for it. Test: `test_wrong_client.py`.
- [ ] **H5. "wrong client" after approving.** Outcome: nothing changes; Kevin told it has gone and needs correcting as a correction. Test: `test_wrong_client.py::TestKevinSaysWrongClient::test_after_approval_it_is_too_late_and_nothing_changes`.
- [ ] **H6. Anything else to the approval email.** Decided by: Claude reads it; code approves only when approval is the one thing asked for and Kevin's words are really in the reply. Outcome: "looks good, send it" sends; "don't send this" cancels; "approve, but make it 150 hours" sends nothing, and Kevin is told the invoice can only go as it is or be cancelled; anything unclear gets a short reply naming "approve", "cancel" and "wrong client" (decision 59). Test: `test_kevin_replies.py::TestApprovalInPlainWords`, `test_emails_and_invoices.py::TestAskFirst::test_anything_else_gets_a_short_reply_asking_for_one_of_the_two_words`.
- [ ] **H7. A reply to a review, in Kevin's own words** ("use 152 hours", "this is for Acme", "approved by Jane on 9/3", "use the new one", "ignore", or several at once). Decided by: Claude reads the reply into typed requests, each quoting his words; code checks each one. Outcome: checks re-run; ready, or asked again; anything beyond a plain hours answer gets a reply saying what was understood and done (decision 59). Test: `test_emails_and_invoices.py::TestReviewReplies`, `test_kevin_replies.py`.
- [ ] **H8. A reply Claude can't make sense of.** Outcome: "Sorry to ask again", with what it understood, what it couldn't do, and one question. Test: `test_emails_and_invoices.py::TestReviewReplies::test_an_unclear_reply_makes_the_agent_ask_again`.
- [ ] **H9. Replies about a set-aside email** -- "try again", "this is from Priya Shah", "ignore", or unclear. Outcome: handled again / handled as hers / closed / asked again; "try again" with nothing new gets *Still needs your review*. An address fixed in QuickBooks without a reply is picked up the next day. Test: `test_engagement_copy.py::TestKevinAnswersAboutAnUnknownAddress`.
- [ ] **H10. An email from Kevin that answers none of the agent's.** Decided by: code -- its reply header names no email of the agent's, and its subject matches none. Outcome: nothing is done with it; Kevin gets one note in the same thread quoting its start and saying how to answer, with a line about how timesheets get read when it had an attachment. An answer to that note (or an out-of-office reply) is not answered again (decision 63). Test: `test_unmatched_replies.py`.
- [ ] **H11. A reply to a review that names an answer not asked about.** Outcome: applied to the closest open question for that record, or asked again. Test: `test_emails_and_invoices.py::TestReviewReplies`.
- [ ] **H12. A different invoice number** ("use 083126MT-MK-revised", "add -revised to it"). Decided by: Claude spells out the number; code checks QuickBooks would take it and no invoice of the agent's holds it. Outcome: the invoice is made under it and comes to Kevin to approve; a number that will not do is refused, saying why (decision 59). Test: `test_kevin_replies.py::TestANumberQuickBooksAlreadyHas`.
- [ ] **H13. "Try again".** Decided by: code -- what the question is about. Outcome: where an invoice or a send failed, it is attempted again now; anywhere else, including a question waiting on QuickBooks for an item's rates, the question stays open, is looked at again on every run, and Kevin is told so (decisions 55 and 61). Test: `test_kevin_replies.py::TestANumberQuickBooksAlreadyHas::test_try_again_after_he_deleted_the_leftover`, `test_kevin_replies.py::test_try_again_on_a_question_about_a_timesheet_leaves_it_open`, `test_kevin_replies.py::test_try_again_never_closes_a_question_waiting_on_rates`.
- [ ] **H14. "Show it to me first".** Outcome: that item goes to Kevin to approve, even where it would have gone automatically. Test: `test_kevin_replies.py::TestANumberQuickBooksAlreadyHas::test_show_me_first_holds_back_an_automatic_invoice`.
- [ ] **H15. A letter or option number** ("A", "option 2", "go with B") on a review that offered ways out. Decided by: code, not Claude -- the letter stands for that option's words, which are then read and checked as if he had written them (decision 61). Test: `test_investigation.py::TestTheInvestigator::test_picking_a_lets_him_answer_with_one_letter`, `test_investigation.py::TestTheInvestigator::test_picking_b_uses_the_new_number`.

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
- [ ] **J4. QuickBooks refuses the invoice** (e.g. duplicate number, account missing). Outcome: `QUICKBOOKS_FAILED`; the timesheet email goes back to Needs Review; nothing reaches the client. For a number QuickBooks already holds, the review says whose invoice holds it -- the agent's own for an item since forgotten, another item's, or one made by hand -- and how to settle it by reply (decision 61). Test: `test_emails_and_invoices.py::TestWhenQuickBooksIsUnhappy`, `test_investigation.py::TestTheRunLooksForItself::test_a_taken_number_says_whose_invoice_holds_it`.
- [ ] **J5. The total QuickBooks makes disagrees with the agent's.** Outcome: voided immediately, both totals reported. Test: `tests/contract/test_quickbooks.py::TestCreateInvoice`.
- [ ] **J6. The client pays.** Decided by: the once-a-day paid check (balance zero). Outcome: the record becomes *client paid*; a partial payment changes nothing. Test: `test_paid_check.py`.
- [ ] **J7. The paid check can't reach QuickBooks.** Outcome: a review; the day is not counted as checked, so the next run asks again. Test: `test_paid_check.py::TestWhenQuickBooksCannotAnswer`.
- [ ] **J7a. An invoice in the agent's records that QuickBooks does not have** (deleted, or made in the sandbox). Decided by: code -- each invoice is asked about on its own. Outcome: that invoice gets one review naming it and the fix (`fops forget`), raised once, not every morning; every other invoice is still checked (decision 61). Test: `test_investigation.py::TestTheRunLooksForItself::test_an_invoice_quickbooks_lacks_gets_its_own_review_and_the_rest_are_checked`, `test_investigation.py::test_a_missing_invoice_is_raised_once_not_every_morning`.
- [ ] **J8. Monday.** Outcome: one summary with the tracking sheet: timesheets received, invoices sent, waiting for review, waiting for approval, no timesheet yet, unpaid invoices past their due date (left out in manual mode), set aside, duplicates filed last week, and -- when there is any -- things that look stuck, each with what to do (decision 64). Test: `test_emails_and_invoices.py::TestMondaySummary`, `test_monday_summary.py`, `test_investigation.py::TestTheRunLooksForItself::test_the_monday_summary_lists_what_looks_stuck`.
- [ ] **J9. The QuickBooks connection getting old** (80+ days). Outcome: a warning at the end of every run. Test: `tests/contract/test_quickbooks.py::TestTokenStore`.

## L. When something is stuck

- [ ] **L1. A review is about to go to Kevin.** Decided by: Claude looks into the item with read-only tools (at most eight turns, three items a run; never an unknown sender or a rate question; no dollar amount ever shown to it); code drops any option to approve, cancel or send, and any "try again" where nothing failed. Outcome: the email gains *What I found* and lettered options, each with the words to reply (decision 61). Test: `test_investigation.py::TestTheInvestigator`, and the eval set `tests/evals/investigations/` (decision 62).
- [ ] **L2. The investigation gives no usable answer** (fails, refuses, runs out of turns, or answers with tags instead of plain fields twice). Outcome: the email goes out exactly as it was. Test: `test_investigation.py::TestTheInvestigator::test_no_answer_leaves_the_email_as_it_was`, `test_investigation.py::test_a_malformed_answer_never_reaches_kevin`, `tests/unit/test_claude_investigator.py`.
- [ ] **L3. The email already started sending.** Outcome: never changed. Test: `test_investigation.py::TestTheInvestigator::test_an_email_already_on_its_way_is_never_changed`.
- [ ] **L4. `fops diagnose`.** Decided by: code only; reads, never writes. Outcome: what is stuck and what to do -- invoices QuickBooks does not have, who holds a taken number, inbox mail the next run will not read and why, items matching no engagement, engagements waiting on too many months (decision 60). Test: `test_diagnosis.py`.

## Gaps found while writing this list

Paths where the agent does less than the other documents say, or nothing at all. Each needs deciding, not just testing.

- ~~**G1. A mailbox that can't be read.**~~ Closed by decision 58 (A6). What is left: if sending is down too -- the same provider -- the email to Kevin waits until it can go. Only the heartbeat (`fops serve`, roadmap PR 14) covers an agent that cannot say anything at all.
- ~~**G2. Claude unreachable.**~~ Closed by decision 58 (D5).
- ~~**G3. Client replies.**~~ Closed by decision 58 (C4): Kevin gets each one as it came.
- ~~**G4. A reply from Kevin the agent can't match.**~~ Closed by decision 63 (H10): he is told nothing was done, and how to answer.
- ~~**G5. The Monday summary.**~~ Closed by decision 64 (J8): it lists duplicates filed last week and unpaid invoices past their due date.
- **G6. `NO_BILLING_CONTACT`** has no whole-run test.
- **G7. A replied-to review with no record behind it** (e.g. a spreadsheet problem): Kevin's answer is recorded but changes nothing; fixing the spreadsheet or QuickBooks is what fixes it.
