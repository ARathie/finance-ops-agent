"""Build the real-format eval cases from invented identities.

The formats here are the ones Icon actually bills through, and they earned
their place: each caught a bug that the made-up timesheets did not (decisions
26, 27). But a format is not a person. **Nothing in this file, and nothing it
writes, is real**: the consultant, the approver, the vendor, its address and
telephone number, the client, and the invoice numbers are all invented. What is
faithfully reproduced is the shape -- the column a vendor heads "WEEK ENDING"
while printing week-*starting* dates, the note assigning a straddling week's
hours to a month, a client system's Sunday-to-Saturday pages spanning two
months, and a timesheet that is a screenshot with no text layer at all.

Run `uv run python tests/evals/build_real_format_cases.py` after changing a
case. `tests/evals/test_real_formats.py` fails if a real name ever reappears.
"""

import io
import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

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

# Invented throughout. Deliberately unlike any real person or company.
CONSULTANT = "Ravi Balakrishnan"
APPROVER = "Lena Ortiz"
CLIENT = "Northwind Utilities"
VENDOR = "Harbour Point Systems, Inc"
VENDOR_ADDRESS = ["2200 Alder Way", "Fremont, CA  94538 USA", "+15105550137"]
VENDOR_EMAIL = "billing@harbourpointsystems.example"
ICON_CONTACT = "Accounts Payable"
END_CLIENT = "State Transport Authority"


@dataclass
class Week:
    starts: date
    hours: int  # hundredths


def money(cents: int) -> str:
    return f"{cents // 100:,}.{cents % 100:02d}"


def invoice_lines(number: str, issued: date, weeks: list[Week], rate: int) -> list[str]:
    total = sum(week.hours for week in weeks)
    amount = total * rate // 100
    lines = [
        VENDOR.upper(),
        *VENDOR_ADDRESS,
        VENDOR_EMAIL,
        f"Invoice  {number}",
        "BILL TO",
        ICON_CONTACT,
        "ICON Technologies Inc",
        "DATE",
        issued.strftime("%m/%d/%Y"),
        "PLEASE PAY",
        f"${money(amount)}",
        "DUE DATE",
        (issued + timedelta(days=30)).strftime("%m/%d/%Y"),
        # The heading says ENDING; every date below is the week's FIRST day.
        "WEEK ENDING FROM AND TO DESCRIPTION HRS RATE AMOUNT",
    ]
    for week in weeks:
        hours = week.hours // 100
        lines.append(
            f"{week.starts.strftime('%m/%d/%Y')} {CLIENT} Consulting Service"
            f" {hours} {money(rate)} {money(hours * rate)}"
        )
    lines.append(f"TOTAL DUE ${money(amount)}")
    lines.append(f"{total // 100} hours * ${rate // 100} = ${money(amount)}")
    return lines


def write_text_pdf(path: Path, pages: list[list[str]]) -> None:
    """A PDF with a real text layer, one object set per page."""

    def escape(text: str) -> str:
        return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")

    streams = []
    for lines in pages:
        content = ["BT /F1 10 Tf 54 750 Td 14 TL"]
        content += [f"({escape(line)}) Tj T*" for line in lines]
        content.append("ET")
        streams.append("\n".join(content).encode("latin-1"))

    page_ids = [3 + index * 2 for index in range(len(streams))]
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [%s] /Count %d >>"
        % (b" ".join(b"%d 0 R" % pid for pid in page_ids), len(streams)),
    ]
    font_id = 3 + len(streams) * 2
    for index, stream in enumerate(streams):
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents %d 0 R"
            b" /Resources << /Font << /F1 %d 0 R >> >> >>" % (page_ids[index] + 1, font_id)
        )
        objects.append(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream))
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

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


def write_screenshot_pdf(path: Path, weeks: list[Week], note: str, pending: Week | None) -> None:
    """The client system's weekly list as an image with no text layer at all,
    the way it really arrives: a screenshot, plus a note written beside the
    week that straddles the month."""
    rows = ([("Pending", pending)] if pending else []) + [("Approved", w) for w in weeks]
    width, height = 1400, 90 + 44 * len(rows)
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    headers = ["Week starts on", "User", "Time Sheet Approver", "Total Hours", "State"]
    for x, text in zip([40, 340, 640, 960, 1120], headers, strict=True):
        draw.text((x, 40), text, fill="black")
    draw.line([(20, 62), (width - 20, 62)], fill="grey")
    for index, (state, week) in enumerate(rows):
        y = 80 + index * 44
        cells = [
            week.starts.strftime("%-m/%d/%Y"),
            CONSULTANT,
            APPROVER,
            str(week.hours // 100),
            state,
        ]
        for x, text in zip([40, 340, 640, 960, 1120], cells, strict=True):
            draw.text((x, y), text, fill="black")
    straddler = next(index for index, (_, w) in enumerate(rows) if w is weeks[-1] or w is weeks[0])
    draw.rectangle([(700, 74 + straddler * 44), (950, 96 + straddler * 44)], outline="red", width=2)
    draw.text((710, 80 + straddler * 44), note, fill="red")
    buffer = io.BytesIO()
    image.save(buffer, format="PDF")
    path.write_bytes(buffer.getvalue())


def field(value: object, quote: str | None = None) -> dict[str, object]:
    return {"value": value, "quote": quote, "confidence": Confidence.HIGH.value}


def save(name: str, reading: TimesheetReading, meta: dict[str, str], codes: list[str]) -> None:
    case = CASES_DIR / name
    case.mkdir(parents=True, exist_ok=True)
    (case / "expected.json").write_text(
        json.dumps({"reading": reading.model_dump(mode="json"), "review_codes": codes}, indent=2)
    )
    (case / "recorded.json").write_text(json.dumps(reading.model_dump(mode="json"), indent=2))
    (case / "meta.json").write_text(json.dumps(meta, indent=2))


def weekly_rows(weeks: list[Week]) -> list[RowEntry]:
    return [
        RowEntry(
            label=week.starts.strftime("%m/%d/%Y"),
            first_day=week.starts,
            last_day=week.starts + timedelta(days=6),
            hours_hundredths=week.hours,
        )
        for week in weeks
    ]


def timesheet_reading(
    weeks: list[Week], month: date, noted: int, pending: Week | None
) -> TimesheetReading:
    rows = weekly_rows(([pending] if pending else []) + weeks)
    return TimesheetReading(
        consultant_name=ReadField[str](
            value=CONSULTANT, quote=CONSULTANT, confidence=Confidence.HIGH
        ),
        client_name=ReadField[str](value=None),
        end_client_name=ReadField[str](value=None),
        period_start=ReadField[date](value=rows[0].first_day, confidence=Confidence.HIGH),
        period_end=ReadField[date](value=rows[-1].last_day, confidence=Confidence.HIGH),
        stated_month_start=ReadField[date](
            value=month, quote=month.strftime("%b-%y"), confidence=Confidence.HIGH
        ),
        noted_in_month_hundredths=ReadField[int](value=noted, confidence=Confidence.HIGH),
        daily_entries=ReadField[list[DailyEntry]](value=[]),
        row_entries=ReadField[list[RowEntry]](value=rows, confidence=Confidence.HIGH),
        stated_total_hours_hundredths=ReadField[int](value=None),
        approval=ReadField[Approval](
            value=Approval(kind=ApprovalKind.APPROVED_STATUS, approver=APPROVER),
            quote="Approved",
            confidence=Confidence.HIGH,
        ),
    )


def invoice_reading(weeks: list[Week], month: date, issued: date) -> TimesheetReading:
    total = sum(week.hours for week in weeks)
    return TimesheetReading(
        consultant_name=ReadField[str](value=None),
        client_name=ReadField[str](value=CLIENT, quote=CLIENT, confidence=Confidence.HIGH),
        end_client_name=ReadField[str](value=None),
        period_start=ReadField[date](value=weeks[0].starts, confidence=Confidence.HIGH),
        period_end=ReadField[date](
            value=weeks[-1].starts + timedelta(days=6), confidence=Confidence.HIGH
        ),
        stated_month_start=ReadField[date](
            value=month, quote=issued.strftime("%m/%d/%Y"), confidence=Confidence.HIGH
        ),
        noted_in_month_hundredths=ReadField[int](value=None),
        daily_entries=ReadField[list[DailyEntry]](value=[]),
        row_entries=ReadField[list[RowEntry]](value=weekly_rows(weeks), confidence=Confidence.HIGH),
        stated_total_hours_hundredths=ReadField[int](
            value=total, quote=f"{total // 100} hours", confidence=Confidence.HIGH
        ),
        approval=ReadField[Approval](value=Approval(kind=ApprovalKind.NONE)),
    )


def daily_pages_reading(days: list[date], month: date) -> TimesheetReading:
    entries = [DailyEntry(day=day, hours_hundredths=800) for day in days]
    return TimesheetReading(
        consultant_name=ReadField[str](
            value=CONSULTANT, quote=CONSULTANT, confidence=Confidence.HIGH
        ),
        client_name=ReadField[str](value=END_CLIENT, quote=END_CLIENT, confidence=Confidence.HIGH),
        end_client_name=ReadField[str](value=None),
        period_start=ReadField[date](value=days[0], confidence=Confidence.HIGH),
        period_end=ReadField[date](value=days[-1], confidence=Confidence.HIGH),
        stated_month_start=ReadField[date](value=month, confidence=Confidence.HIGH),
        noted_in_month_hundredths=ReadField[int](value=None),
        daily_entries=ReadField[list[DailyEntry]](value=entries, confidence=Confidence.HIGH),
        row_entries=ReadField[list[RowEntry]](value=[]),
        stated_total_hours_hundredths=ReadField[int](value=None),
        approval=ReadField[Approval](
            value=Approval(kind=ApprovalKind.APPROVED_STATUS, approver=APPROVER),
            quote="Status: Approved",
            confidence=Confidence.HIGH,
        ),
    )


def build() -> None:
    rate = 11_000
    # August: the week starting 29 Aug runs into September; 8 of its hours are August.
    aug_weeks = [Week(date(2026, 8, 1) + timedelta(weeks=n), 4_000) for n in range(4)]
    aug_invoice = [*aug_weeks, Week(date(2026, 8, 29), 800)]
    aug_sheet = [*aug_weeks, Week(date(2026, 8, 29), 4_000)]
    # July: the week starting 27 June straddles; 16 of its 32 hours are July.
    jul_weeks = [Week(date(2026, 7, 4) + timedelta(weeks=n), 4_000) for n in range(4)]
    jul_invoice = [Week(date(2026, 6, 27), 1_600), *jul_weeks]
    jul_sheet = [Week(date(2026, 6, 27), 3_200), *jul_weeks]

    (CASES_DIR / "44-pdf-weekly-list-aug").mkdir(parents=True, exist_ok=True)
    write_screenshot_pdf(
        CASES_DIR / "44-pdf-weekly-list-aug" / "input.pdf",
        aug_sheet,
        "8 hours in Aug",
        Week(date(2026, 9, 5), 800),
    )
    save(
        "44-pdf-weekly-list-aug",
        timesheet_reading(aug_sheet, date(2026, 8, 1), 800, Week(date(2026, 9, 5), 800)),
        {
            "time_system": "client weekly timesheet list (system name unknown)",
            "origin": "real_format_invented_data",
            "notes": "A screenshot with no text layer. Column headed 'Week starts on'. The"
            " week of 8/29 runs into September and a note beside it says 8 hours are August."
            " A later week is still Pending and must not be billed.",
        },
        [],
    )

    (CASES_DIR / "45-pdf-weekly-list-jul").mkdir(parents=True, exist_ok=True)
    write_screenshot_pdf(
        CASES_DIR / "45-pdf-weekly-list-jul" / "input.pdf", jul_sheet, "16 Hours in Jul-26", None
    )
    save(
        "45-pdf-weekly-list-jul",
        timesheet_reading(jul_sheet, date(2026, 7, 1), 1_600, None),
        {
            "time_system": "client weekly timesheet list (system name unknown)",
            "origin": "real_format_invented_data",
            "notes": "The same list for July. The week of 6/27 holds 32 hours of which a note"
            " assigns 16 to July, and the previous week is printed for context.",
        },
        [],
    )

    for name, weeks, month, number, issued in (
        ("46-pdf-vendor-invoice-aug", aug_invoice, date(2026, 8, 1), "2411", date(2026, 8, 31)),
        ("47-pdf-vendor-invoice-jul", jul_invoice, date(2026, 7, 1), "2410", date(2026, 7, 31)),
    ):
        (CASES_DIR / name).mkdir(parents=True, exist_ok=True)
        write_text_pdf(CASES_DIR / name / "input.pdf", [invoice_lines(number, issued, weeks, rate)])
        save(
            name,
            invoice_reading(weeks, month, issued),
            {
                "time_system": "vendor invoice with weekly lines",
                "origin": "real_format_invented_data",
                "notes": "The column is headed 'WEEK ENDING' while every date below it is the"
                " week's FIRST day. The straddling week is already apportioned by the vendor,"
                " and the invoice date names the month being billed.",
            },
            # Alone it is not billable: a total and a month, but nothing on it
            # shows the client approved the hours.
            ["NO_APPROVAL"],
        )

    # A client system that prints one page per week, Sunday to Saturday, so a
    # month's pages carry days from the months either side.
    worked = [
        date(2025, 4, 28),
        date(2025, 4, 29),
        date(2025, 4, 30),
        date(2025, 5, 1),
        date(2025, 5, 2),
    ]
    for start in (date(2025, 5, 5), date(2025, 5, 12), date(2025, 5, 19)):
        worked += [start + timedelta(days=n) for n in range(5)]
    worked += [date(2025, 5, 27), date(2025, 5, 28), date(2025, 5, 29), date(2025, 5, 30)]
    pages = []
    for week in range(5):
        sunday = date(2025, 4, 27) + timedelta(weeks=week)
        days = [day for day in worked if sunday <= day <= sunday + timedelta(days=6)]
        pages.append(
            [
                "Timesheet Information",
                f"Timesheet for: {CONSULTANT}   Client: {END_CLIENT}",
                f"Reports To: {APPROVER}",
                "Requisition Title: Senior Business Systems Analyst",
                f"Status: Approved   Approved By: {APPROVER}",
                *[f"{day.isoformat()}  08:00" for day in days],
                f"Total Billable Hours (Day) {len(days) * 8}:00",
                f"Period (Begin - End): {sunday:%A, %B %-d, %Y}"
                f" - {sunday + timedelta(days=6):%A, %B %-d, %Y}",
            ]
        )
    (CASES_DIR / "48-pdf-daily-pages-may").mkdir(parents=True, exist_ok=True)
    write_text_pdf(CASES_DIR / "48-pdf-daily-pages-may" / "input.pdf", pages)
    save(
        "48-pdf-daily-pages-may",
        daily_pages_reading(worked, date(2025, 5, 1)),
        {
            "time_system": "client daily timesheet, one page per week",
            "origin": "real_format_invented_data",
            "notes": "Five pages, each a Sunday-to-Saturday week. The first begins 27 April, so"
            " 24 of the document's 192 hours belong to April and only 168 to May. Summing the"
            " pages billed 192 (decision 27).",
        },
        [],
    )
    print(f"built the real-format cases in {CASES_DIR}")


if __name__ == "__main__":
    build()
