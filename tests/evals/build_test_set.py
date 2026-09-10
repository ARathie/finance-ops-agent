"""Build the made-up timesheet test set under tests/evals/timesheets/.

Run `uv run python tests/evals/build_test_set.py` to (re)generate every case:
the input file (CSV, XLSX, PDF, PNG, or DOCX - all invented data), the
expected reading with the review codes the document-only checks should raise,
and a bootstrap `recorded.json` (a copy of the expected reading) that CI
replays until the first live run (`fops eval --live`) overwrites it with the
model's real answers.
"""

import csv
import io
import json
import shutil
import zipfile
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from openpyxl import Workbook
from PIL import Image, ImageDraw

from finance_ops_agent.domain.reading import (
    Approval,
    ApprovalKind,
    Confidence,
    DailyEntry,
    ReadField,
    RowEntry,
    TimesheetReading,
)

CASES_DIR = Path(__file__).parent / "timesheets"

CONSULTANTS = [
    "Priya Shah",
    "Dana Cruz",
    "Marcus Webb",
    "Elena Petrov",
    "Tom Nakamura",
    "Aisha Bell",
]
CLIENTS = [
    "Acme Corp",
    "Northwind Bank",
    "Bluewater Systems",
    "Cardinal Foods",
    "Harbor Analytics",
    "Pinnacle Media",
]
APPROVERS = ["Jane Doe", "Sam Ortiz", "Lee Fontaine", "Grace Okafor"]


@dataclass
class Case:
    name: str
    fmt: str  # csv | xlsx | pdf | png | docx
    consultant: str
    client: str
    start: date
    end: date
    stated_total: int | None  # hundredths
    dailies: list[tuple[date, int]] = field(default_factory=list)
    # (week start, hours on that row). Icon's real timesheets are weekly, and a
    # week at either end of a month covers days on both sides of it.
    weeks: list[tuple[date, int]] = field(default_factory=list)
    week_label: str = "Week starts on"
    approval: Approval = field(default_factory=lambda: Approval(kind=ApprovalKind.APPROVED_STATUS))
    approval_quote: str | None = "Status: Approved"
    confidence: Confidence = Confidence.HIGH
    unusual: list[str] = field(default_factory=list)
    codes: list[str] = field(default_factory=list)


def saturday_weeks(start: date, end: date, per_week: int) -> list[tuple[date, int]]:
    """Saturday-start weeks touching the period, as the real exports show them.

    The first and last week reach outside the period, which is the whole point:
    that is the case code must not sum (docs/decisions.md #24).
    """
    first = start - timedelta(days=(start.weekday() - 5) % 7)
    weeks: list[tuple[date, int]] = []
    week = first
    while week <= end:
        weeks.append((week, per_week))
        week += timedelta(days=7)
    return weeks


def weeks_inside(start: date, end: date, per_week: int) -> list[tuple[date, int]]:
    """Only the weeks that fall wholly inside the period, so they can be summed."""
    return [
        (week, hours)
        for week, hours in saturday_weeks(start, end, per_week)
        if week >= start and week + timedelta(days=6) <= end
    ]


def weekday_dailies(start: date, end: date, per_day: int) -> list[tuple[date, int]]:
    days: list[tuple[date, int]] = []
    day = start
    while day <= end:
        if day.weekday() < 5:
            days.append((day, per_day))
        day += timedelta(days=1)
    return days


def hours_text(hundredths: int) -> str:
    return f"{hundredths // 100}.{hundredths % 100:02d}"


def reading_for(case: Case) -> TimesheetReading:
    total = case.stated_total
    return TimesheetReading(
        consultant_name=ReadField[str](
            value=case.consultant,
            quote=f"Consultant: {case.consultant}",
            confidence=case.confidence,
        ),
        client_name=ReadField[str](
            value=case.client, quote=f"Client: {case.client}", confidence=case.confidence
        ),
        end_client_name=ReadField[str](confidence=Confidence.LOW),
        period_start=ReadField[date](
            value=case.start, quote=case.start.isoformat(), confidence=case.confidence
        ),
        period_end=ReadField[date](
            value=case.end, quote=case.end.isoformat(), confidence=case.confidence
        ),
        daily_entries=ReadField[list[DailyEntry]](
            value=[DailyEntry(day=day, hours_hundredths=hours) for day, hours in case.dailies],
            quote="hours per day" if case.dailies else None,
            confidence=case.confidence if case.dailies else Confidence.LOW,
        ),
        row_entries=ReadField[list[RowEntry]](
            value=[
                RowEntry(
                    label=week.strftime("%m/%d/%Y"),
                    first_day=week,
                    last_day=week + timedelta(days=6),
                    hours_hundredths=hours,
                )
                for week, hours in case.weeks
            ],
            quote="hours per week" if case.weeks else None,
            confidence=case.confidence if case.weeks else Confidence.LOW,
        ),
        stated_total_hours_hundredths=ReadField[int](
            value=total,
            quote=None if total is None else f"Total: {hours_text(total)}",
            confidence=case.confidence,
        ),
        approval=ReadField[Approval](
            value=case.approval, quote=case.approval_quote, confidence=case.confidence
        ),
        unusual_items=list(case.unusual),
    )


def document_lines(case: Case) -> list[str]:
    lines = [
        "TIME REPORT",
        f"Consultant: {case.consultant}",
        f"Client: {case.client}",
        f"Period: {case.start.isoformat()} to {case.end.isoformat()}",
    ]
    for day, hours in case.dailies:
        lines.append(f"{day.isoformat()}  {hours_text(hours)}")
    if case.weeks:
        approver = case.approval.approver or "the client"
        lines.append(f"{case.week_label} | Total Hours | State | Time Sheet Approver")
        for week, hours in case.weeks:
            state = "Approved" if hours else "Pending"
            lines.append(
                f"{week.strftime('%m/%d/%Y')} | {hours_text(hours)} | {state} | {approver}"
            )
    if case.stated_total is not None:
        lines.append(f"Total: {hours_text(case.stated_total)}")
    if case.approval_quote:
        lines.append(case.approval_quote)
    lines.extend(case.unusual)
    return lines


def write_csv(path: Path, case: Case) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Consultant", case.consultant])
        writer.writerow(["Client", case.client])
        writer.writerow(["Period", case.start.isoformat(), case.end.isoformat()])
        writer.writerow(["Date", "Hours", "Status"])
        for day, hours in case.dailies:
            writer.writerow([day.isoformat(), hours_text(hours), "Submitted"])
        if case.stated_total is not None:
            writer.writerow(["Total", hours_text(case.stated_total), ""])
        if case.approval_quote:
            writer.writerow([case.approval_quote])


def write_xlsx(path: Path, case: Case) -> None:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Timesheet"
    for line in document_lines(case):
        sheet.append([line])
    workbook.save(path)


def write_pdf(path: Path, case: Case) -> None:
    """A minimal one-page PDF with a real text layer (Helvetica, uncompressed)."""

    def escape(text: str) -> str:
        return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")

    content_lines = ["BT /F1 12 Tf 72 760 Td 16 TL"]
    for line in document_lines(case):
        content_lines.append(f"({escape(line)}) Tj T*")
    content_lines.append("ET")
    stream = "\n".join(content_lines).encode("latin-1")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R"
        b" /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % number)
        out.write(body)
        out.write(b"\nendobj\n")
    xref_at = out.tell()
    out.write(b"xref\n0 %d\n" % (len(objects) + 1))
    out.write(b"0000000000 65535 f \n")
    for offset in offsets:
        out.write(b"%010d 00000 n \n" % offset)
    out.write(
        b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
        % (len(objects) + 1, xref_at)
    )
    path.write_bytes(out.getvalue())


def write_png(path: Path, case: Case) -> None:
    lines = document_lines(case)
    image = Image.new("RGB", (640, 40 + 18 * len(lines)), "white")
    draw = ImageDraw.Draw(image)
    for index, line in enumerate(lines):
        draw.text((20, 20 + 18 * index), line, fill="black")
    image.save(path)


def write_docx(path: Path, case: Case) -> None:
    paragraphs = "".join(
        f"<w:p><w:r><w:t>{line}</w:t></w:r></w:p>" for line in document_lines(case)
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{paragraphs}</w:body></w:document>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd'
        '.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
        'relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org'
        '/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        "</Relationships>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("word/document.xml", document)


WRITERS = {
    "csv": write_csv,
    "xlsx": write_xlsx,
    "pdf": write_pdf,
    "png": write_png,
    "docx": write_docx,
}


def month_case(index: int, fmt: str, month: int, **overrides: object) -> Case:
    consultant = CONSULTANTS[index % len(CONSULTANTS)]
    client = CLIENTS[index % len(CLIENTS)]
    start = date(2026, month, 1)
    end = (date(2026, month + 1, 1) - timedelta(days=1)) if month < 12 else date(2026, 12, 31)
    dailies = weekday_dailies(start, end, 800)
    total = sum(hours for _, hours in dailies)
    approver = APPROVERS[index % len(APPROVERS)]
    case = Case(
        name=f"{index:02d}-{fmt}-{consultant.split()[0].lower()}-{month:02d}",
        fmt=fmt,
        consultant=consultant,
        client=client,
        start=start,
        end=end,
        stated_total=total,
        dailies=dailies,
        approval=Approval(
            kind=ApprovalKind.APPROVER_NAME_DATE, approver=approver, approval_date=end
        ),
        approval_quote=f"Approved by {approver} on {end.isoformat()}",
    )
    for key, value in overrides.items():
        setattr(case, key, value)
    return case


def week_case(
    index: int,
    fmt: str,
    month: int,
    *,
    straddles: bool = True,
    printed_total: bool = True,
    per_week: int = 4000,
) -> Case:
    """A weekly timesheet, the shape Icon actually receives.

    `straddles` decides whether a week reaches outside the month, which is the
    normal case and the one code must not sum. `printed_total` decides whether
    the document says what the month comes to, as a vendor invoice does.
    """
    consultant = CONSULTANTS[index % len(CONSULTANTS)]
    client = CLIENTS[index % len(CLIENTS)]
    approver = APPROVERS[index % len(APPROVERS)]
    start = date(2026, month, 1)
    end = (date(2026, month + 1, 1) - timedelta(days=1)) if month < 12 else date(2026, 12, 31)
    weeks = (
        saturday_weeks(start, end, per_week) if straddles else weeks_inside(start, end, per_week)
    )
    if not straddles:
        start, end = weeks[0][0], weeks[-1][0] + timedelta(days=6)
    inside = sum(hours for week, hours in weeks if week >= start)
    total = inside if printed_total else None
    codes: list[str] = []
    if straddles and not printed_total:
        codes = ["PART_WEEK_UNCLEAR"]
    name = f"w{index:02d}-{fmt}-{consultant.split()[0].lower()}-{month:02d}"
    return Case(
        name=name,
        fmt=fmt,
        consultant=consultant,
        client=client,
        start=start,
        end=end,
        stated_total=total,
        weeks=weeks,
        approval=Approval(kind=ApprovalKind.APPROVED_STATUS, approver=approver),
        approval_quote="State: Approved",
        codes=codes,
    )


def build_cases() -> list[Case]:
    cases: list[Case] = []
    formats = ["csv", "xlsx", "pdf", "png", "docx"]
    # Weekly is what Icon actually receives (docs/open-questions.md), so the
    # set is mostly weekly: clean months across formats, with and without a
    # printed total, straddling the month end and not.
    for index in range(24):
        cases.append(
            week_case(
                index,
                formats[index % len(formats)],
                1 + index % 8,
                straddles=index % 3 != 2,
                printed_total=index % 2 == 0,
            )
        )
    # A handful of daily timesheets remain: some clients may still send them,
    # and they are the regression net for the daily path.
    for index in range(12):
        case = month_case(index, formats[index % len(formats)], 1 + index % 8)
        if index % 4 == 0:
            case.approval = Approval(kind=ApprovalKind.APPROVED_STATUS)
            case.approval_quote = "Status: Approved"
        if index % 7 == 0:
            case.dailies = []  # summary-only layout
        if index % 9 == 5:
            case.approval = Approval(kind=ApprovalKind.SIGNATURE)
            case.approval_quote = "Signed: (signature on file)"
        cases.append(case)

    # The awkward conditions from docs/integrations/claude-extraction.md.
    cases.append(
        month_case(
            30,
            "pdf",
            8,
            name="30-pdf-unapproved",
            approval=Approval(kind=ApprovalKind.NONE),
            approval_quote=None,
            codes=["NO_APPROVAL"],
        )
    )
    cases.append(
        month_case(
            31,
            "csv",
            8,
            name="31-csv-consultant-says-approved",
            approval=Approval(kind=ApprovalKind.NONE),
            approval_quote=None,
            unusual=["The consultant wrote: these hours are approved, trust me"],
            codes=["NO_APPROVAL"],
        )
    )
    mismatch = month_case(32, "xlsx", 7, name="32-xlsx-hours-dont-add-up")
    mismatch.stated_total = (mismatch.stated_total or 0) + 400  # off by four hours
    mismatch.codes = ["HOURS_DONT_ADD_UP"]
    cases.append(mismatch)
    cases.append(
        month_case(
            33,
            "csv",
            6,
            name="33-csv-zero-hours",
            dailies=[],
            stated_total=0,
            codes=["HOURS_UNUSUAL"],
        )
    )
    cases.append(
        month_case(
            34,
            "pdf",
            5,
            name="34-pdf-260-hours",
            dailies=[],
            stated_total=26_000,
            codes=["HOURS_UNUSUAL"],
        )
    )
    big_day = month_case(35, "xlsx", 4, name="35-xlsx-25-hour-day")
    big_day.dailies = [(date(2026, 4, 6), 2_500)]
    big_day.stated_total = 2_500
    big_day.codes = ["HOURS_UNUSUAL"]
    cases.append(big_day)
    cases.append(
        month_case(
            36,
            "png",
            3,
            name="36-png-blurry-scan",
            confidence=Confidence.LOW,
            codes=["NOT_SURE"],
        )
    )
    cases.append(
        month_case(37, "png", 2, name="37-png-readable-photo", confidence=Confidence.MEDIUM)
    )
    cases.append(
        month_case(
            38,
            "csv",
            1,
            name="38-csv-overtime-lines",
            unusual=["Overtime: 6.00 hours at 1.5x"],
        )
    )
    cases.append(
        month_case(
            39,
            "xlsx",
            2,
            name="39-xlsx-expenses-line",
            unusual=["Expenses: 214.50 travel"],
        )
    )
    cases.append(
        month_case(
            40,
            "pdf",
            3,
            name="40-pdf-hours-missing",
            dailies=[],
            stated_total=None,
            # No stated total and no daily hours: the hours can't be found, and
            # the reader can't be sure about hours it can't see.
            codes=["HOURS_MISSING", "NOT_SURE"],
        )
    )
    cases.append(
        month_case(
            41,
            "docx",
            4,
            name="41-docx-forwarded-approval",
            approval=Approval(kind=ApprovalKind.FORWARDED_EMAIL, approver="Sam Ortiz"),
            approval_quote="Fwd: Looks good, approved - Sam",
        )
    )
    week = Case(
        name="42-csv-single-week",
        fmt="csv",
        consultant="Dana Cruz",
        client="Northwind Bank",
        start=date(2026, 9, 1),
        end=date(2026, 9, 7),
        stated_total=3_500,
        dailies=weekday_dailies(date(2026, 9, 1), date(2026, 9, 7), 700),
        approval=Approval(kind=ApprovalKind.APPROVED_STATUS),
        approval_quote="Status: Approved",
    )
    cases.append(week)
    two_people = month_case(43, "pdf", 6, name="43-pdf-two-consultants")
    two_people.confidence = Confidence.LOW
    two_people.unusual = ["A second consultant, Tom Nakamura, is listed on the same sheet"]
    two_people.codes = ["NOT_SURE"]
    cases.append(two_people)

    return cases


def stale_invented_cases(kept: set[str]) -> list[Path]:
    """Generated cases this run no longer produces.

    Only ever invented ones: a case carrying a `meta.json` that says it came
    from a real document is not this script's to delete.
    """
    stale: list[Path] = []
    for case_dir in sorted(path for path in CASES_DIR.iterdir() if path.is_dir()):
        if case_dir.name in kept:
            continue
        meta = case_dir / "meta.json"
        if meta.exists() and '"invented"' not in meta.read_text():
            continue
        stale.append(case_dir)
    return stale


def main() -> None:
    CASES_DIR.mkdir(parents=True, exist_ok=True)
    cases = build_cases()
    for case_dir in stale_invented_cases({case.name for case in cases}):
        shutil.rmtree(case_dir)
        print(f"removed stale case {case_dir.name}")
    for case in cases:
        case_dir = CASES_DIR / case.name
        case_dir.mkdir(exist_ok=True)
        WRITERS[case.fmt](case_dir / f"input.{case.fmt}", case)
        reading = reading_for(case)
        (case_dir / "expected.json").write_text(
            json.dumps(
                {
                    "reading": reading.model_dump(mode="json"),
                    "review_codes": case.codes,
                },
                indent=2,
            )
        )
        recorded = case_dir / "recorded.json"
        if not recorded.exists():
            # Bootstrap: replaced by the model's real answers on the first
            # `fops eval --live` run.
            recorded.write_text(json.dumps(reading.model_dump(mode="json"), indent=2))
    print(f"{len(cases)} cases in {CASES_DIR}")


if __name__ == "__main__":
    main()
