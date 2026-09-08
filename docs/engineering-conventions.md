# Engineering conventions

## Tooling

- Python 3.11+, managed with `uv` (`uv sync`, `uv run …`). One `pyproject.toml` at the root; `src/` layout; package `finance_ops_agent`; console script `fops`.
- `ruff` for lint and format (`uv run ruff check .`, `uv run ruff format .`; line length 100, rule families E, W, F, I, UP, B, SIM), `mypy --strict` on `domain/`, `application/`, and `ports/` (adapters may relax where third-party types are missing, via per-module overrides in `pyproject.toml`; today strict applies everywhere), `pytest`.
- GitHub Actions runs all four on every push, on Ubuntu and macOS with Python 3.11 and 3.13; the tests themselves need no network.
- Dependencies are pinned in `uv.lock`; the Anthropic SDK is `anthropic` 1.x.

## Layout

```
src/finance_ops_agent/{domain,application,ports,adapters,cli}
tests/unit          fast tests of domain rules and application steps with fakes
tests/contract      the same test suite run against each fake and (when credentials exist) each real adapter
tests/scenarios     whole runs on fixture mailboxes: happy path, duplicate, correction, unknown sender, no approval, …
tests/evals         made-up timesheets with expected readings, for the Claude reader
tests/fixtures      .eml files, sample workbooks, sample timesheets (all made up; never real client data)
alembic/            database migrations (arrives with the database in PR 4)
docs/               these documents
```

## Naming

- `bill_rate_cents` and `pay_rate_cents`, never `rate`. `hours_hundredths`, never `hours` as a float.
- Statuses and review codes exactly as spelled in `status-tracking.md` and `timesheet-checks.md`; define each once as a `StrEnum` in `domain/`.
- Kevin-facing text (emails, tracking sheet headers, messages in review emails) uses the plain words from `glossary.md`; no accounting jargon.
- Adapter modules are named after the real thing: `adapters/microsoft365`, `adapters/quickbooks`, `adapters/claude`, `adapters/excel`, `adapters/sqlite`, `adapters/pdf`, `adapters/fakes`.

## Types and values

- Pydantic v2 for anything that crosses a boundary (the timesheet reading, config, provider payloads). Frozen dataclasses for domain values (`Money`, `Hours`, `BillingPeriod`).
- Integers for money (cents) and hours (hundredths) everywhere; `ROUND_HALF_UP` where division is unavoidable.
- `datetime.date` for periods and due dates; timezone-aware UTC `datetime` for timestamps; the `Clock` port for "today".
- Enums are `StrEnum`; JSON columns hold Pydantic-validated dicts.

## Tests

- Every business rule and every status change has a unit test. The worked example from `context/04_business_rules_and_edge_cases.md` (156 h, $140, $100) is a test.
- Fakes over mocks: a fake implements the port fully and is used by scenario tests; `MagicMock` is not used for ports.
- Real adapters are tested with recorded HTTP responses (saved JSON with secrets removed) and, when `FOPS_LIVE_TESTS=1` and credentials exist, a small live smoke test that never sends to a client.
- Snapshot tests for every email template.
- Scenario tests run twice to prove a second run changes nothing.
- No real names, rates, or emails from Icon in the repository.

## Git and PRs

- Conventional commit messages (`feat:`, `fix:`, `docs:`, `test:`, `chore:`).
- Small PRs that match the roadmap; each PR ticks its boxes in `roadmap.md` and updates any doc whose behaviour it changes.
- A new decision that departs from `decisions.md` gets a new numbered entry there, in the same PR.

## Definition of done for a PR

1. `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, `uv run pytest` pass locally and in CI.
2. Tests cover the new rule or step, including the failure paths that become review items.
3. Fakes are updated whenever a port changes.
4. Docs and the roadmap are updated in the same PR.
5. No credentials, no real client data, no network needed for the tests.
