# Microsoft 365 mailbox

The agent's mailbox lives in Icon's Microsoft 365. The agent reads and sends mail through Microsoft Graph. This file is the setup checklist and the technical rules for the `microsoft365` adapter. Check endpoint details against the current Microsoft Graph documentation when implementing; the shapes below are the intended design.

## One-time setup (Icon's Microsoft 365 admin)

1. Create the agent mailbox (for example `jay@icon-technologies.com`) as a normal licensed user or a shared mailbox.
2. In Microsoft Entra ID, register an application (single tenant). Note the tenant id and client id; create a client secret (set a reminder for its expiry) and store it in the agent's `.env`.
3. Grant **application** permissions (not delegated): `Mail.ReadWrite` and `Mail.Send`. Admin-consent them.
4. Limit the app to the agent mailbox only, so it can never read anyone else's mail. In Exchange Online PowerShell:
   - create a mail-enabled security group containing only the agent mailbox;
   - `New-ApplicationAccessPolicy -AppId <client id> -PolicyScopeGroupId <group address> -AccessRight RestrictAccess -Description "Billing agent"`;
   - `Test-ApplicationAccessPolicy -AppId <client id> -Identity kevin@icon-technologies.com` must report Denied.
5. In the agent mailbox, create folders `Agent/Processed`, `Agent/Needs Review`, `Agent/Ignored` (the agent creates them if missing).
6. Run `fops doctor`: it acquires a token, reads the agent inbox, confirms it **cannot** read a second mailbox, and sends one test email to Kevin.

## Reading mail

- Token: `msal.ConfidentialClientApplication(client_id, authority="https://login.microsoftonline.com/<tenant>", client_credential=secret).acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])`. Cache in memory per run.
- Always send the header `Prefer: IdType="ImmutableId"` so message ids do not change when a message is moved between folders. Store both the immutable `id` and `internetMessageId`.
- New mail: `GET /users/{mailbox}/mailFolders/inbox/messages/delta?$select=id,internetMessageId,conversationId,from,toRecipients,ccRecipients,subject,receivedDateTime,hasAttachments,isDraft`. Follow `@odata.nextLink`; save `@odata.deltaLink` in the `state` table only after every message in the page has been stored. On `410 Gone` (delta expired) start again without a token; this is safe because messages already stored are skipped by id.
- Body: `GET /users/{mailbox}/messages/{id}?$select=body,uniqueBody` with `Prefer: outlook.body-content-type="text"`.
- Attachments: `GET /users/{mailbox}/messages/{id}/attachments` (file attachments carry `contentBytes` when small; otherwise `GET …/attachments/{attachmentId}/$value`). Skip `isInline` images unless the message has no other attachment. Store every file by sha256.
- Folder moves: `POST /users/{mailbox}/messages/{id}/move` with `destinationId`. Folders are only a convenience for Kevin looking at the mailbox; the agent's database is the record. A message dragged back by a human is not reprocessed (its id is already known).

## Sending mail

Because attachments can exceed the size limit of the single-call send, always use the three-step send:

1. `POST /users/{mailbox}/messages` to create a draft: subject, HTML and text body, `toRecipients`, `ccRecipients` (Kevin always on client emails), `replyTo` (Kevin on client emails), and `internetMessageHeaders: [{"name": "x-fops-action", "value": "<outgoing row id>"}]`. Save the returned draft id on the `outgoing` row immediately.
2. Add attachments: `POST /users/{mailbox}/messages/{draftId}/attachments` with `@odata.type: #microsoft.graph.fileAttachment` and base64 `contentBytes` for files under 3 MB; for larger files `POST …/attachments/createUploadSession` and upload in chunks.
3. `POST /users/{mailbox}/messages/{draftId}/send`. Then mark the `outgoing` row done.

Restart safety: an `outgoing` row that is `in_flight` with a draft id is reconciled by `GET /users/{mailbox}/messages/{draftId}?$select=isDraft,sentDateTime`: still a draft → finish steps 2–3; sent → mark done; not found → create again.

Replies to Kevin's messages stay in the same thread: `POST /users/{mailbox}/messages/{id}/createReply`, then patch the body and add attachments, then send.

## Throttling and errors

- On `429` or `503`, wait for `Retry-After` seconds and retry; give up for this run after 3 tries and try again next run.
- `401` → get a new token once; if it fails again, raise `MAILBOX_PROBLEM` (emailed to Kevin at most once a day if the mailbox still sends; always written to the log and shown by `fops status`).
- Never disable TLS verification.

## Testing

- The `EmailInbox` and `EmailSender` fakes are folders of `.eml` files; scenario tests use them.
- The real adapter is tested against recorded JSON responses (a small recorder script saves real responses with secrets removed) for: delta paging, delta expiry, attachment download, draft + upload session + send, 429 backoff, reconcile of an in-flight draft.
- A live smoke test (`FOPS_LIVE_TESTS=1`) reads the inbox and sends one email to Kevin only.

## Library choice

`msal` for tokens and a small `httpx` client for the eight or so endpoints above. The generated `msgraph-sdk` is large and hard to record and replay; it is not used.
