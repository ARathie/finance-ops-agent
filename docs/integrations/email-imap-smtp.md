# The mailbox: IMAP and SMTP at Rackspace Email

Icon's email is hosted at Rackspace Email. Kevin's account settings show `secure.emailsrvr.com`, IMAP on port 993 and SMTP on port 465, both with SSL, and a plain password login. That is an ordinary mailbox spoken to with the two standard email protocols: **IMAP** to read, **SMTP** to send. No Microsoft 365 or Google account is involved, and Kevin's own mail program (Outlook, Apple Mail, a phone) does not matter: the agent has its own mailbox, and Kevin's replies reach it from whatever he uses.

This file is the setup checklist and the technical rules for the `email` adapter (`adapters/email/`). It replaces the Microsoft Graph design that PR 8 built on a wrong assumption (decision 21). Check details against Rackspace's current help pages when implementing; the shapes below are the intended design.

## One-time setup (whoever administers Icon's email at Rackspace)

1. In the Rackspace Email control panel, add a mailbox for the agent (for example `jay@icon-technologies.com`). It is an ordinary mailbox with a password; there is nothing to register, consent to, or scope. Confirm it is a **Rackspace Email** (IMAP) mailbox like Kevin's, not a Hosted Exchange one.
2. Choose a long random password. If Rackspace offers an app-specific password or two-step verification for the mailbox, use it (open question). Put the address and password in the agent's `.env` as `MAIL_USERNAME` and `MAIL_PASSWORD`; never in git, never in an email.
3. Nobody else uses this mailbox, and it is not added to Kevin's mail program. If someone does look inside, moving or deleting mail by hand is harmless (the database is the record), but it is confusing.
4. Set `MAIL_START_DATE` to the first day the agent should care about, so the first run does not read months of old mail.
5. Run `fops doctor`: it logs in over IMAP and SMTP, lists the folders, creates the `Agent/…` folders if they are missing, checks that the username is the agent's address (not Kevin's), and with `--send-test-email` sends one email to Kevin and confirms a copy landed in the Sent folder.

Because the credentials belong to one mailbox, the agent cannot reach anyone else's mail. There is no separate "scoping" step and nothing to prove beyond "the username is the agent's address".

## Settings

| Setting | Default | Meaning |
|---|---|---|
| `MAIL_IMAP_HOST`, `MAIL_IMAP_PORT` | `secure.emailsrvr.com`, `993` | IMAP over SSL |
| `MAIL_SMTP_HOST`, `MAIL_SMTP_PORT`, `MAIL_SMTP_SECURITY` | `secure.emailsrvr.com`, `465`, `ssl` | SMTP over SSL; `587` with `starttls` also works |
| `MAIL_USERNAME`, `MAIL_PASSWORD` | required | the agent mailbox's address and password |
| `MAIL_START_DATE` | the first run's date | ignore mail that arrived before this date |
| `MAIL_FOLDER_PREFIX` | `Agent` | parent of the `Processed`, `Needs Review`, and `Ignored` folders |
| `MAIL_SENT_FOLDER` | detected | where sent copies go; detected from the server's `\Sent` folder flag, else this |

`FOPS_AGENT_MAILBOX` (the address the agent sends from) must equal `MAIL_USERNAME`; `fops doctor` checks.

## Reading mail

- Library: `imapclient`, a well-kept Python IMAP library (the standard `imaplib` is too low-level to read comfortably). Parsing uses the standard `email` package with `policy=email.policy.default`, the same parser the fake inbox already uses on `.eml` files.
- One IMAP connection per run; no IDLE. Every 15 minutes is plenty.
- **What "new" means.** IMAP numbers the messages in a folder with UIDs that only grow, under a folder-wide `UIDVALIDITY` stamp. The `state` table keeps `(UIDVALIDITY, last UID seen)` for `INBOX`. A run searches `UID <last+1>:*` limited to `SINCE <MAIL_START_DATE>`, fetches each new message with `BODY.PEEK[]` (so the agent never marks mail as read by itself), stores it, and only then advances the last UID. If `UIDVALIDITY` changes (the server rebuilt the folder), the run starts from UID 1 again; that is safe because messages already stored are skipped by their Message-ID.
- **Message identity.** UIDs change when a message is moved between folders, so they are never the key. The key is the `Message-ID` header (trimmed, angle brackets kept), with the SHA-256 of the raw message as a second check. A message with no `Message-ID` (rare; broken senders) gets a synthetic one from the hash of its `From`, `Date`, `Subject`, and body. The `messages` table stores the Message-ID (unique), the raw SHA-256, `In-Reply-To`, and `References`; the raw bytes go in the file store, which also satisfies "the bytes forwarded to the client are the original attachment bytes".
- **Attachments.** `iter_attachments()` on the parsed message. Inline images (`Content-Disposition: inline` with a `Content-ID`) are ignored unless the message has no other attachment. Each file is stored by its SHA-256.
- **Folders.** The agent creates `Agent/Processed`, `Agent/Needs Review`, and `Agent/Ignored`, under `INBOX` or at the top level depending on the server, after discovering the hierarchy delimiter with `LIST`. Moves use `UID MOVE` when the server advertises `MOVE`, else `COPY` + `\Deleted` + `EXPUNGE`. After each email is handled it is filed: a timesheet that checked out or a reply from Kevin → `Processed`; anything that produced a review item for Kevin, or came from an address the agent does not know → `Needs Review`; a duplicate timesheet → `Ignored`. Folders are a convenience for a human looking at the mailbox; the database is the record, and a message dragged back by hand is not reprocessed.
- **The agent's own mail.** Anything from the agent's own address is skipped by the poller (a bounce or an auto-reply can quote it).

## Sending mail

- Library: the standard `smtplib` (`SMTP_SSL` on 465, or `starttls` on 587) with `email.message.EmailMessage`. One SMTP session per run, opened when the first email is due.
- **The agent greets the server before looking for a login.** A server does not list its extensions until it has been sent `EHLO`, and `SMTP_SSL`'s constructor reads the greeting without sending one, so a login guarded by `has_extn("auth")` would be skipped on the very path Rackspace uses. The session would then be unauthenticated and the server would refuse every recipient as relaying. `open_smtp` says `EHLO` on both paths, and refuses to send at all if a server other than a plain-text test server on this machine offers no login.
- Every outgoing email gets a `Message-ID` the agent makes itself (`email.utils.make_msgid(domain=<the agent's domain>)`), stored on the `outgoing` row **before** anything is sent. Replies to Kevin set `In-Reply-To` and `References` to his message's ID so they thread in his mail program.
- The sequence for each email: write the `outgoing` row (`pending`, with the Message-ID) in the same transaction as the status change → mark `in_flight` with a start time → SMTP `send_message` (the server's `250` reply means it has taken responsibility for delivery) → record `accepted_at` immediately → `APPEND` a copy to the Sent folder with `\Seen` → mark `done`. The `APPEND` is a courtesy so the Sent folder is complete; if it fails the email was still sent, and the next run retries only the `APPEND`.
- **Never twice, with SMTP.** SMTP gives no handle to ask "did that go?", so the guarantee is stated precisely: a resend can only happen if the process dies in the instant between the server's `250` and the `accepted_at` write. On start-up, an `in_flight` row with no `accepted_at` is reconciled: search the Sent folder for its Message-ID; found → mark `done`. Not found and younger than 10 minutes → leave it for the next run. Not found and older → the agent does not guess: it opens a `SEND_UNCERTAIN` review asking Kevin (who is on CC of every client email) whether his copy arrived. "received" marks it done; "resend" sends it again with the **same** Message-ID so mail programs that de-duplicate can. A plain failure before `250` (connection refused, timeout, login rejected) is simply retried next run, up to three attempts, then `SEND_FAILED`. A recipient rejected by the server (`SMTPRecipientsRefused`) is permanent: `SEND_FAILED` at once, naming the address together with the code and reason the server gave. The reason is part of the message because the address alone does not say what to do next: "no such mailbox" means the address is wrong, while "relaying denied" or a sending limit means the address is right and the mailbox is not allowed to send there.
- Keep each message under 20 MB (Rackspace's limit is higher; timesheets and invoices are small). Kevin's copy of every client email is a CC, not a BCC, so the client sees it.

## Kevin's replies

Kevin replies from his own mailbox to an email the agent sent. The reply arrives in the agent's `INBOX` with `In-Reply-To` (and `References`) naming the agent's Message-ID; Outlook, Apple Mail, and phones all set these. The agent matches on that first and on the subject as a fallback (decision 17). Only replies from Kevin's address count; approval replies must start with `approve` or `cancel`; review answers are read by Claude into typed answers as before.

## Errors

- Login rejected (IMAP `AUTHENTICATIONFAILED`, SMTP `535`): `MAILBOX_PROBLEM`, written to the log and shown by `fops doctor` and `fops status`; emailed to Kevin only if SMTP still works. The heartbeat (`running-it.md`) is what tells someone when the whole mailbox is unreachable.
- Connection errors and timeouts: retry with a short backoff within the run (three tries), then give up for this run and try again next run.
- Rackspace's sending limits are far above this agent's volume (a few dozen emails a month). If the server ever answers `4xx` to a send, treat it as transient and retry next run.
- TLS verification is never disabled.

## Testing

- The fakes are unchanged in spirit: `EmailInbox` from a folder of `.eml` files, `EmailSender` writing `.eml` files to an outbox folder, both passing the port's contract tests.
- The real adapter is tested against a **local mail server**, never against Rackspace: GreenMail's standalone jar (Java; IMAP and SMTP with the users `jay@icon-technologies.com` and `kevin@icon-technologies.com`), started by the test fixture in `tests/contract/conftest.py` on free local ports, in plain text on `127.0.0.1` (the one place `MAIL_*_SECURITY=none` is allowed). CI installs Java and downloads the jar (`tests/contract/get_greenmail.py`, cached); locally run that script once, or the mail server tests skip. When `CI` is set they fail instead of skipping. `tests/contract/test_email.py` covers every case in the reading and sending sections: new-message polling and last-UID advance; `UIDVALIDITY` change → full rescan with no duplicate rows; a redelivered message skipped by Message-ID; a message without a Message-ID; the attachment and inline-image rules; the agent's own mail skipped; `MAIL_START_DATE`; folder creation with the server's delimiter (and, with a stubbed server, `/` under `INBOX`); `MOVE` and the copy-delete fallback; send → `250` → `accepted_at` → Sent copy; reconcile for each of the three outcomes (found, too young, `SEND_UNCERTAIN`) and "received" / "resend" from Kevin, the resend under the same Message-ID; a rejected recipient → `SEND_FAILED`; reply matching by `In-Reply-To` and by subject; `fops doctor` with and without `--send-test-email`, with a wrong password, and with Kevin's address instead of the agent's; the SSL path greeting the server and logging in, and a server offering no login being refused rather than sent to (a stubbed server, because the local test server only exercises the plain-text path); TLS verification on (and `ssl` against a plain-text port fails rather than falling back).
- The same "never twice" cases run on the fakes in `tests/scenarios/test_emails_and_invoices.py`, with a simulated crash just before and just after the server takes the email.
- A live smoke test, `tests/contract/test_live_mailbox.py`, runs only with `FOPS_LIVE_TESTS=1`: it is `fops doctor --send-test-email` against the real mailbox, so it logs in over IMAP and SMTP, lists the folders, and sends one email to Kevin only. It never touches a client address. If Rackspace ever behaves differently from GreenMail, the difference is reproduced in a unit test with a stubbed server (as the `/`-under-`INBOX` folder test does), never with recordings of Icon's real mail.

## What changed from the Microsoft 365 design

No app registration, admin consent, or access policy: a mailbox and a password. No delta queries or immutable ids: UIDs plus Message-IDs. No draft-then-send: a self-made Message-ID recorded before the send, the Sent folder for reconcile, and Kevin as the last resort. No Microsoft library. The `microsoft365` adapter, its fixtures, and its tests are removed by PR 11.
