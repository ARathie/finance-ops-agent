# Reading timesheets with Claude

The agent uses Claude (Anthropic's model) for three narrow jobs: deciding what kind of email arrived, reading a timesheet into a fixed form, and reading Kevin's short replies into a structured answer. Everything else is ordinary code. This file is the technical contract for the `claude` adapter.

## Rules

- The model reads; it never decides. It never adds up hours (code does), never computes money, never sees a rate, never picks the engagement, never picks recipients, and is never given tools.
- Email bodies and attachments are untrusted input. The prompt tells the model to treat their contents as data, not instructions, and the structured output means nothing it says can become an action except through the checks in `timesheet-checks.md`.
- Every request and response is saved (`timesheets.reading`, plus a `data/files/` copy of the raw response) with the model id, the prompt version, and token usage, so any reading can be replayed or audited.
- One model for all three jobs, set by `FOPS_MODEL` (default `claude-opus-5`). Do not pick a cheaper model for "simple" calls without measuring on the test set first.

## SDK usage (Python `anthropic` 1.x)

- Client: `anthropic.Anthropic()` (reads `ANTHROPIC_API_KEY`). `anthropic` 1.x is built on `httpx2`; do not pass `httpx` objects to it. The agent's own Microsoft and QuickBooks clients use `httpx` separately.
- Structured reading: `client.messages.parse(model=FOPS_MODEL, max_tokens=16000, system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}], messages=[...], output_format=TimesheetReading)` and use `response.parsed_output`. The system prompt is fixed text (no dates, no ids) so it caches; the document goes in the user message after it.
- Thinking is on by default for this model, and the default effort is `high`; the adapter relies on those defaults rather than passing `output_config` alongside `parse` (a lower effort may be enough for clean system exports — tune on the test set before changing it).
- Attachments: PDFs as `{"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": ...}}` (limit 32 MB per request and 600 pages; refuse larger files with `CANT_READ_ATTACHMENT`); images as `{"type": "image", "source": {"type": "base64", "media_type": "image/png", ...}}` (downscale very large screenshots first); spreadsheets (`.xlsx`, `.csv`) are converted to text tables with `openpyxl` (first 500 rows per sheet) and sent as text; `.docx` is converted to text; anything else is `CANT_READ_ATTACHMENT`.
- Always check `response.stop_reason` before using the output: `refusal` (see `response.stop_details`) and `max_tokens` mean the attachment could not be read → `CANT_READ_ATTACHMENT` with the reason; never accept a partial reading. Server-side refusal fallbacks are not enabled for reading timesheets, so a reading always comes from the configured model.
- Citations cannot be combined with structured output, so evidence is part of the form: every field carries `quote` (the words on the page) and `confidence` (`high` / `medium` / `low`). When the PDF has a text layer, the code checks that each quote actually appears in the extracted text (`pypdf`) and lowers the confidence if it does not.
- Errors: catch in order `anthropic.RateLimitError` (retry after `retry-after`), `anthropic.APIStatusError` with status ≥ 500 (retry), `anthropic.APIConnectionError` (retry), `anthropic.BadRequestError` (permanent → `CANT_READ_ATTACHMENT`). The SDK retries twice on its own; the run retries across runs up to 3 times before raising a review item. Log `response._request_id` on failures.

## The three calls

### 1. What kind of email is this?

Input: sender, subject, the first part of the body, attachment names. Output (structured): one of `timesheet`, `corrected_timesheet`, `approval_from_client` (a client manager's approval with no timesheet attached), `reply_from_kevin`, `client_reply`, `other`, plus a one-line reason. Code first applies simple rules (sender is Kevin and the message is in a known thread → reply; sender unknown → set aside) so the model only sees the ambiguous ones.

### 2. Read the timesheet

Input: the attachment (see above), the email text, and, for spelling only, the list of consultant names and client names from the engagement list (no rates, no emails). Output: `TimesheetReading` (see `technical-design.md`): consultant name, client name, end client name, period start and end, daily entries, stated total, approval (kind, approver, date), unusual items, and quote + confidence per field.

### 3. Read Kevin's reply

Input: Kevin's reply text and the review reasons that were asked. Output (structured): for each reason, the answer as a typed value (`consultant_name`, `client_name`, `period_start` / `period_end`, `hours`, `approval_note`, `ignore`, `use_new_one`, `unclear`). Code applies it; `unclear` means the agent asks again. Approval replies (`approve` / `cancel`) are matched by code without the model.

## Prompt versions

Prompts live in `adapters/claude/prompts/` as versioned files (`timesheet_v3.md`). The version string is stored with every reading. Changing a prompt or the model requires running the test set and keeping the scores at or above the recorded thresholds.

## Test set

`tests/evals/timesheets/<case>/` holds a made-up timesheet (`input.pdf|xlsx|csv|png|docx`), `expected.json` (the correct reading and the review codes the document-only checks — hours, approval, confidence — should raise), and `recorded.json` (the reader's saved answer). The set has 44 cases across layouts (day-by-day exports, spreadsheets, rendered scans, summary-only sheets, forwarded approvals) and conditions (unapproved, hours that don't add up, zero and implausible hours, missing hours, low-confidence scans, overtime and expense lines, two consultants in one file); `tests/evals/build_test_set.py` regenerates it deterministically.

`fops eval` runs the set and reports per-field accuracy, mean hours error, and review codes raised versus expected, then compares against the floor recorded in `tests/evals/thresholds.json` (exit code 1 below it). CI replays `recorded.json` only — no network, no key.

**The live run** is manual: `ANTHROPIC_API_KEY=... uv run fops eval --live` calls the real model for every case (this costs money) and overwrites each case's `recorded.json` with the model's actual answers. Until the first live run, `recorded.json` is a bootstrap copy of the expected reading, so the recorded scores are perfect by construction; after the first live run, re-record `thresholds.json` from the real scores, and from then on a prompt or model change must keep the scores at or above them. The Batches API can halve the live run's cost if the set grows large.
