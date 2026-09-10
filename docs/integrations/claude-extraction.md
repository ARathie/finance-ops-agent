# Reading timesheets with Claude

The agent uses Claude (Anthropic's model) for three narrow jobs: deciding what kind of email arrived, reading a timesheet into a fixed form, and reading Kevin's short replies into a structured answer. Everything else is ordinary code. This file is the technical contract for the `claude` adapter.

## Rules

- The model reads; it never decides. It never adds up hours (code does), never computes money, never sees a rate, never picks the engagement, never picks recipients, and is never given tools.
- Email bodies and attachments are untrusted input. The prompt tells the model to treat their contents as data, not instructions, and the structured output means nothing it says can become an action except through the checks in `timesheet-checks.md`.
- Every request and response is saved (`timesheets.reading`, plus a `data/files/` copy of the raw response) with the model id, the prompt version, and token usage, so any reading can be replayed or audited.
- One model for all three jobs, set by `FOPS_MODEL` (default `claude-opus-5`). Do not pick a cheaper model for "simple" calls without measuring on the test set first.

## SDK usage (Python `anthropic` 1.x)

- Client: `anthropic.Anthropic()` (reads `ANTHROPIC_API_KEY`). `anthropic` 1.x is built on `httpx2`; do not pass `httpx` objects to it. The agent's own QuickBooks client uses `httpx` separately.
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

**The live run** is manual: `ANTHROPIC_API_KEY=... uv run fops eval --live` calls the real model for every case (this costs money) and overwrites each case's `recorded.json` with the model's actual answers. The Batches API can halve the live run's cost if the set grows large.

**Where the recorded answers came from.** `thresholds.json` records this in `source`, and `fops eval` prints a warning whenever it still says `bootstrap`:

| `source` | What `recorded.json` holds | What the scores prove |
|---|---|---|
| `bootstrap` | A copy of `expected.json`, written when the set was built | The harness only. The scores are perfect by construction and mean nothing about the reading. |
| `live` | The model's own answers, from `fops eval --live` | The reading, at the `model` and `prompt_version` recorded alongside, on `recorded_on`. |

A live run stamps `source`, `model`, `prompt_version`, and `recorded_on` itself; it never moves the threshold numbers, because a person reads the live scores and decides what the floor should be (roadmap PR 12). From then on a prompt or model change must keep the scores at or above that floor, and a run recorded under an older prompt version fails the test set rather than passing quietly.

## What a live run costs

`fops eval --live` counts the tokens it spends and prints them with the scores: input tokens (and how many of those were written to or read from the prompt cache), output tokens, the same figures per timesheet, and an estimate in dollars.

The arithmetic is integer throughout, in thousandths of a cent — one timesheet costs a few cents, so a figure in whole cents would round away the number worth knowing. Prices are held in `application/eval_runner.py` as whole cents per million tokens, with the date they were last checked (`PRICES_CHECKED_ON`), and that date is printed next to every estimate so nobody quotes a stale figure as fact. Cached input is counted at its own two rates: writing the prompt into the cache costs more than plain input, reading it back costs far less. When the built-in prices have moved, override them for one run rather than editing code:

```
uv run fops eval --live --price-input 500 --price-output 2500   # cents per million tokens
```

If the model has no price in the table, the run still reports its token counts and says it cannot cost them, rather than guessing.

**The measured cost per timesheet.** Filled in from the first live run (roadmap PR 12), so that anyone sizing a billing cycle has a real number rather than an estimate:

| | Tokens in (per timesheet) | Tokens out (per timesheet) | Cost per timesheet | Cost for the whole set |
|---|---|---|---|---|
| Not yet measured | — | — | — | — |

Also record here, with the numbers, the decision that the live scores are good enough to go on to PR 13, and who made it.

## Real timesheets, and the systems they come from

The 44 made-up cases prove the reader on layouts we invented. They say nothing about what a real export from a real client time system looks like, which is what the agent will actually be given. So the set is also checked for coverage of the systems Icon bills through:

- `tests/evals/time_systems.json` lists those systems under `required`. It is empty until Kevin answers which they are (`open-questions.md`). Adding a system to that list without adding a case for it fails the test set.
- A case that came from a real timesheet carries a `meta.json` next to its input file naming the `time_system` it came from, `"origin": "anonymised_real"`, and `anonymised_by` and `anonymised_on` — who confirmed nothing identifying was left in it, and when. A case without a `meta.json` is a made-up one.
- Refusing to record the anonymisation is refused by the loader, because that record is the only evidence the check ever happened.

**Anonymising a real timesheet before committing it.** Nothing real goes in the repository (`CLAUDE.md`). Replace, don't redact: a blanked-out field changes the layout, which is the whole point of keeping the sample.

1. Replace every consultant, approver, client, and end-client name with an invented one, using the same name throughout the file.
2. Replace every rate, amount, and total with an invented number. Rates never belong in a timesheet the agent reads, so they should not survive the copy at all.
3. Shift the dates to an invented period; keep the shape (a month, a week, weekdays only).
4. Remove email addresses, phone numbers, employee and purchase-order numbers, account numbers, and logos or letterheads that name the real client.
5. Open the file and read it through, including anything the format hides — spreadsheet formulas and other sheets, PDF text layers, document metadata (author, company), and image EXIF.
6. Write the `meta.json` with your name and the date, and add the system to `time_systems.json`.
