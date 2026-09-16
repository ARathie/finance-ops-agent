"""The real-format cases carry real *shapes* and invented everything else.

These formats earned their place by each catching a bug the made-up timesheets
missed (decisions 26, 27). The risk they bring is the obvious one: the quickest
way to add a format is to commit the document it came from. The repository is
public and Icon's consultants, clients and vendors did not agree to that, so
this fails if a name we have seen on a real document ever appears.
"""

import json
from pathlib import Path

import pytest

from finance_ops_agent.adapters.excel.engagement_list import _cell_text  # noqa: F401
from finance_ops_agent.domain.checks import check_hours
from finance_ops_agent.domain.periods import BillingPeriod
from finance_ops_agent.domain.reading import TimesheetReading

CASES = Path(__file__).parent / "timesheets"
REAL_FORMAT = sorted(case for case in CASES.iterdir() if (case / "meta.json").is_file())

# Words seen on documents Icon actually sent us, kept as truncated SHA-256 so
# that the guard against committing them is not itself a commit of them. A
# case is scanned word by word; the invented stand-ins are in
# build_real_format_cases.py, and none of them hashes to anything here.
NEVER_COMMIT = {
    "069eb887fabc2b1c",
    "276e8dfa962aed86",
    "441f2ad09747015c",
    "67ddc6b53e8faf22",
    "6973bd2f467a0bd0",
    "7a48277bd5e0829e",
    "a5ae1936ae87e179",
    "af232ac2139ce3b2",
    "ca86c4ad52b2f87c",
    "d1d97953dfa81a08",
    "e4bb71e7d4647c5f",
    "e64599505dbfbc70",
    "f6f9c5f004b4e5b5",
    "f8daffab459eb267",
    "f90e9f3755b06a98",
    "fd504b4f5a94e6d8",
    "ff050464ac89c6ef",
}


def texts(case: Path) -> str:
    """Everything a case holds, as lowercase text: readings, notes, and the
    document's own text layer."""
    parts = [path.read_text() for path in case.glob("*.json")]
    for pdf in case.glob("*.pdf"):
        from pypdf import PdfReader

        parts += [page.extract_text() or "" for page in PdfReader(pdf).pages]
    return "\n".join(parts).lower()


def test_there_are_real_format_cases() -> None:
    assert len(REAL_FORMAT) >= 5, [case.name for case in REAL_FORMAT]


@pytest.mark.parametrize("case", REAL_FORMAT, ids=lambda case: case.name)
def test_no_real_name_is_committed(case: Path) -> None:
    import hashlib
    import re

    tokens = re.findall(r"[a-z0-9]+", texts(case))
    # Single words and adjacent pairs, so a one-word name and a two-word company
    # name are both seen.
    words = set(tokens) | {f"{a} {b}" for a, b in zip(tokens, tokens[1:], strict=False)}
    found = {
        word for word in words if hashlib.sha256(word.encode()).hexdigest()[:16] in NEVER_COMMIT
    }
    assert not found, f"{case.name} carries real data: {sorted(found)}"


@pytest.mark.parametrize("case", REAL_FORMAT, ids=lambda case: case.name)
def test_each_says_where_it_came_from(case: Path) -> None:
    meta = json.loads((case / "meta.json").read_text())
    assert meta["origin"] == "real_format_invented_data"
    assert meta["time_system"] and meta["notes"]


def reading_of(case: Path) -> TimesheetReading:
    return TimesheetReading.model_validate(
        json.loads((case / "expected.json").read_text())["reading"]
    )


@pytest.mark.parametrize(
    ("name", "period", "hours"),
    [
        ("44-pdf-weekly-list-aug", ("2026-08-01", "2026-08-31"), 16_800),
        ("45-pdf-weekly-list-jul", ("2026-07-01", "2026-07-31"), 17_600),
        ("46-pdf-vendor-invoice-aug", ("2026-08-01", "2026-08-31"), 16_800),
        ("47-pdf-vendor-invoice-jul", ("2026-07-01", "2026-07-31"), 17_600),
        ("48-pdf-daily-pages-may", ("2025-05-01", "2025-05-31"), 16_800),
    ],
)
def test_each_format_comes_to_the_right_hours(
    name: str, period: tuple[str, str], hours: int
) -> None:
    """The numbers the real documents came to: 176 for July, 168 for August,
    168 for May. Every one of these was wrong at some point today."""
    from datetime import date

    reading = reading_of(CASES / name)
    wanted = BillingPeriod(date.fromisoformat(period[0]), date.fromisoformat(period[1]))
    total, findings = check_hours(reading, wanted)
    assert total == hours, f"{name}: {total} not {hours}"
    assert findings == [], f"{name}: {[f.code.value for f in findings]}"
