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
- Thinking is on by default for this model; set `output_config={"effort": "high"}` explicitly and tune on the test set (`medium` may be enough for clean system exports).
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

`tests/evals/timesheets/<case>/` holds a made-up timesheet (`input.pdf|xlsx|png|eml`) and `expected.json` (the correct reading and the review codes the checks should raise). Aim for 40+ cases across layouts (system exports such as Fieldglass-style reports, spreadsheets, scanned or photographed sheets, day-by-day vs summary-only, forwarded approval emails) and conditions (unapproved, partially approved, corrected, duplicate, two periods in one file, overtime lines, several consultants in one file). `fops eval` runs the set and reports per-field accuracy, hours error, and which review codes were raised versus expected. CI replays recorded responses; the live run is manual and can use the Batches API for half price.
