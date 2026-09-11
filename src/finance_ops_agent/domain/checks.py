"""The checks a timesheet goes through, as pure functions with no I/O.

Each returns what it found plus Findings (a review code and the message for
Kevin). The application runs them in the order from docs/timesheet-checks.md
and sends one review email listing everything wrong with a timesheet.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TypeVar

from finance_ops_agent.domain.engagements import Consultant, Engagement
from finance_ops_agent.domain.periods import BillingPeriod, period_containing
from finance_ops_agent.domain.reading import (
    ApprovalKind,
    Confidence,
    ReadField,
    RowEntry,
    TimesheetReading,
)
from finance_ops_agent.domain.review import REVIEW_MESSAGES, ReviewCode

# How far a weekly timesheet may reach past the month it bills: one whole
# context week at each end, as Icon's exports print (decision 26).
OVERHANG_DAYS = 8

T = TypeVar("T")

QUARTER_HOUR_HUNDREDTHS = 25
FULL_DAY_HUNDREDTHS = 800  # full time is 8 hours per weekday
UNUSUAL_OVER_FULL_TIME = 125  # per cent: more than 25% over full time looks unusual
MAX_DAY_HUNDREDTHS = 2400


@dataclass(frozen=True)
class Finding:
    code: ReviewCode
    message: str


def _name_tokens(name: str) -> frozenset[str]:
    return frozenset(re.sub(r"[^\w\s]", " ", name).casefold().split())


def names_match(one: str, other: str) -> bool:
    """Ignore case, punctuation, and the order of first and last name."""
    tokens_one, tokens_other = _name_tokens(one), _name_tokens(other)
    return bool(tokens_one) and tokens_one == tokens_other


def match_consultant(
    sender: str, reading: TimesheetReading, consultants: list[Consultant]
) -> tuple[Consultant | None, list[Finding]]:
    """The sender's address decides; failing that, the name on the timesheet."""
    sender_key = sender.strip().casefold()
    for consultant in consultants:
        if sender_key in (email.casefold() for email in consultant.emails):
            return consultant, []
    name = reading.consultant_name.value
    if name:
        matches = [
            consultant
            for consultant in consultants
            if any(names_match(name, known) for known in (consultant.name, *consultant.other_names))
        ]
        if len(matches) == 1:
            return matches[0], []
    return None, [
        Finding(ReviewCode.CONSULTANT_UNKNOWN, "I can't tell which consultant this is for.")
    ]


def _covers(engagement: Engagement, start: date, end: date) -> bool:
    if not engagement.active:
        return False
    if end < engagement.start_date:
        return False
    return engagement.end_date is None or start <= engagement.end_date


def _client_named(reading: TimesheetReading, client_names: list[str]) -> bool:
    for read_name in (reading.client_name.value, reading.end_client_name.value):
        if read_name and any(names_match(read_name, known) for known in client_names if known):
            return True
    return False


def match_engagement(
    consultant: Consultant,
    reading: TimesheetReading,
    engagements: list[Engagement],
    client_names_by_client: dict[str, list[str]],
) -> tuple[str | None, list[Finding]]:
    """Which client this is for: the one active engagement covering the dates,
    or the one the timesheet names. Returns the client name."""
    start, end = reading.period_start.value, reading.period_end.value
    if start is None or end is None:
        return None, [Finding(ReviewCode.PERIOD_UNCLEAR, "I can't tell which dates this covers.")]
    covering = {
        engagement.client
        for engagement in engagements
        if names_match(engagement.consultant, consultant.name) and _covers(engagement, start, end)
    }
    if len(covering) == 1:
        return covering.pop(), []
    if len(covering) > 1:
        named = [
            client
            for client in covering
            if _client_named(reading, client_names_by_client.get(client, [client]))
        ]
        if len(named) == 1:
            return named[0], []
    return None, [
        Finding(
            ReviewCode.ENGAGEMENT_UNCLEAR,
            "I can't tell which client this is for,"
            " or there is no active engagement for these dates.",
        )
    ]


def rate_row_in_force(
    rows: list[Engagement], period: BillingPeriod
) -> tuple[Engagement | None, list[Finding]]:
    """The row whose "Rates from" is the latest on or before the period's first
    day. A rate change in the middle of the period is a review, not a split
    invoice (docs/engagement-list.md)."""
    in_force = [row for row in rows if row.rates_from <= period.start]
    if not in_force:
        return None, [
            Finding(ReviewCode.RATE_MISSING, "The engagement list has no rate for these dates.")
        ]
    row = max(in_force, key=lambda r: r.rates_from)
    changed_mid_period = [r for r in rows if period.start < r.rates_from <= period.end]
    if changed_mid_period:
        rows_text = ", ".join(str(r.row_number) for r in changed_mid_period)
        return None, [
            Finding(
                ReviewCode.LIST_ROW_PROBLEM,
                f"The rate changes in the middle of this billing period (row {rows_text});"
                " I won't split an invoice. Which rate should apply?",
            )
        ]
    return row, []


def _anchor_day(reading: TimesheetReading) -> date | None:
    """A day inside the month being billed, from the surest source available.

    Decision 26. A document that names its month settles it. Otherwise the
    month holding most of the timesheet's days wins: a weekly timesheet always
    spills a few days into a neighbouring month, and those few never outvote
    the month it is for.
    """
    stated = reading.stated_month.value
    if stated is not None:
        return date(stated.year, stated.month, 15)
    start, end = reading.period_start.value, reading.period_end.value
    if start is None or end is None or end < start:
        return None
    days_per_month: dict[tuple[int, int], int] = {}
    day = start
    while day <= end:
        key = (day.year, day.month)
        days_per_month[key] = days_per_month.get(key, 0) + 1
        day += timedelta(days=1)
    year, month = max(days_per_month, key=lambda key: (days_per_month[key], key))
    return date(year, month, 15)


def fit_billing_period(
    row: Engagement, reading: TimesheetReading
) -> tuple[BillingPeriod | None, list[Finding]]:
    """The billing period this timesheet is for (decision 26).

    Icon's timesheets are weekly, so their own dates run past both ends of the
    month being billed and can never sit inside one period. The period comes
    from the month the documents name, or failing that from where most of the
    timesheet's days fall; the rows are then allowed to overhang it, and how
    many of a straddling week's hours belong here is settled by `check_hours`,
    never by trimming dates.
    """
    anchor = _anchor_day(reading)
    if anchor is None:
        return None, [Finding(ReviewCode.PERIOD_UNCLEAR, "I can't tell which dates this covers.")]
    try:
        period = period_containing(
            anchor,
            row.billing_schedule,
            row.start_date,
            row.end_date,
            row.first_period_start,
        )
    except ValueError:
        return None, [
            Finding(
                ReviewCode.PERIOD_MISMATCH,
                "The dates don't line up with the billing schedule for this engagement.",
            )
        ]
    start, end = reading.period_start.value, reading.period_end.value
    reaches_past = (
        start is not None
        and end is not None
        and ((period.start - start).days > OVERHANG_DAYS or (end - period.end).days > OVERHANG_DAYS)
    )
    if reaches_past and not (reading.row_entries.value or []):
        # Weekly rows are what make an overhang normal (decision 26). Without
        # them, dates running well past the period mean the wrong period.
        return None, [
            Finding(
                ReviewCode.PERIOD_MISMATCH,
                "The dates don't line up with the billing schedule for this engagement.",
            )
        ]
    return period, []


def _weekdays(start: date, end: date) -> int:
    count, day = 0, start
    while day <= end:
        if day.weekday() < 5:
            count += 1
        day += timedelta(days=1)
    return count


def _sort_rows(
    reading: TimesheetReading, period: BillingPeriod
) -> tuple[list[RowEntry], list[RowEntry]]:
    """The weekly rows wholly inside the billing period, and those straddling it.

    A row wholly outside is neither: a monthly timesheet printed from a time
    system shows the weeks either side too (June's last week on July's sheet),
    and those hours belong to another invoice. Counting them would overbill.
    """
    inside: list[RowEntry] = []
    straddling: list[RowEntry] = []
    for row in reading.row_entries.value or []:
        if row.first_day is None or row.last_day is None:
            continue
        if row.last_day < period.start or row.first_day > period.end:
            continue  # another month's week, printed for context
        if row.first_day >= period.start and row.last_day <= period.end:
            inside.append(row)
        else:
            straddling.append(row)
    return inside, straddling


def check_hours(
    reading: TimesheetReading, period: BillingPeriod | None = None
) -> tuple[int | None, list[Finding]]:
    """The hours to invoice (hundredths), plus anything wrong with them.

    The printed total is what gets invoiced (decision 24). With a billing
    period given, the weekly rows are judged against it rather than against the
    timesheet's own span, and a note assigning a straddling week's hours to the
    month is checked against the printed total rather than trusted over it
    (decision 26).
    """
    stated = reading.stated_total_hours_hundredths.value
    summed = reading.summed_daily_hundredths()
    rows = reading.summed_row_hundredths()
    findings: list[Finding] = []
    noted = reading.noted_in_month_hundredths.value
    if period is not None:
        inside, straddling_rows = _sort_rows(reading, period)
        rows = sum(row.hours_hundredths for row in inside) if inside else None
        if straddling_rows and noted is not None:
            # Rows wholly inside, plus what the note assigns to this month:
            # code adds it up, and only to check the printed total (decision 26).
            from_note = (rows or 0) + noted
            if stated is not None and abs(stated - from_note) > QUARTER_HOUR_HUNDREDTHS:
                findings.append(
                    Finding(
                        ReviewCode.PART_WEEK_DISAGREES,
                        REVIEW_MESSAGES[ReviewCode.PART_WEEK_DISAGREES],
                    )
                )
                # That finding names the discrepancy exactly; leave the rows
                # agreeing with the total so it is not also reported vaguely.
                rows = stated
            else:
                if stated is None:
                    stated = from_note
                # The note has settled the straddling week, so the rows now
                # stand for the whole month and match the printed total.
                rows = from_note
            straddling_rows = []
        straddling = straddling_rows
    else:
        straddling = reading.rows_outside_period()
    # A weekly row running past the period's end holds hours from both periods.
    # Summing it would overbill, and how it splits is never the model's to
    # guess (decision 24): the printed total settles it, or Kevin does.
    row_total = None if straddling else rows
    total = stated if stated is not None else (summed if summed is not None else row_total)
    if total is None:
        if straddling:
            return None, [
                Finding(
                    ReviewCode.PART_WEEK_UNCLEAR,
                    REVIEW_MESSAGES[ReviewCode.PART_WEEK_UNCLEAR],
                )
            ]
        return None, [
            Finding(ReviewCode.HOURS_MISSING, "I can't find the hours on this timesheet.")
        ]
    if stated is not None and summed is not None and abs(stated - summed) > QUARTER_HOUR_HUNDREDTHS:
        findings.append(
            Finding(
                ReviewCode.HOURS_DONT_ADD_UP,
                "The daily hours don't add up to the total.",
            )
        )
    if (
        stated is not None
        and rows is not None
        and not straddling
        and abs(stated - rows) > QUARTER_HOUR_HUNDREDTHS
    ):
        findings.append(
            Finding(
                ReviewCode.HOURS_DONT_ADD_UP,
                "The weekly hours don't add up to the total.",
            )
        )
    entries = reading.daily_entries.value or []
    unusual = total == 0 or any(entry.hours_hundredths > MAX_DAY_HUNDREDTHS for entry in entries)
    start: date | None
    end: date | None
    if period is not None:
        start, end = period.start, period.end
    else:
        start, end = reading.period_start.value, reading.period_end.value
    if not unusual and start is not None and end is not None and end >= start:
        full_time = _weekdays(start, end) * FULL_DAY_HUNDREDTHS
        unusual = total * 100 > full_time * UNUSUAL_OVER_FULL_TIME
    if unusual:
        findings.append(
            Finding(ReviewCode.HOURS_UNUSUAL, "The hours look unusually high, or are zero.")
        )
    return total, findings


def check_approval(reading: TimesheetReading) -> list[Finding]:
    approval = reading.approval.value
    if approval is None or approval.kind is ApprovalKind.NONE:
        return [
            Finding(ReviewCode.NO_APPROVAL, "I can't see that the client approved these hours.")
        ]
    return []


def check_confidence(reading: TimesheetReading) -> list[Finding]:
    """Low confidence on who, when, how many hours, or approval is a review."""
    hours_confidence = (
        reading.stated_total_hours_hundredths.confidence
        if reading.stated_total_hours_hundredths.value is not None
        else reading.daily_entries.confidence
    )
    unsure = [
        label
        for label, confidence in (
            ("the consultant", reading.consultant_name.confidence),
            ("the dates", reading.period_start.confidence),
            ("the dates", reading.period_end.confidence),
            ("the hours", hours_confidence),
            ("the approval", reading.approval.confidence),
        )
        if confidence is Confidence.LOW
    ]
    if not unsure:
        return []
    fields = ", ".join(dict.fromkeys(unsure))
    return [
        Finding(
            ReviewCode.NOT_SURE,
            f"I read this timesheet but I'm not confident about {fields}.",
        )
    ]


def covered_days(spans: list[tuple[date, date]]) -> set[date]:
    days: set[date] = set()
    for start, end in spans:
        day = start
        while day <= end:
            days.add(day)
            day += timedelta(days=1)
    return days


def period_fully_covered(period: BillingPeriod, spans: list[tuple[date, date]]) -> bool:
    """Whether the timesheets seen so far cover every day of the billing period
    (weekly timesheets into a monthly invoice wait here until they do)."""
    covered = covered_days(spans)
    day = period.start
    while day <= period.end:
        if day not in covered:
            return False
        day += timedelta(days=1)
    return True


def _kind_of(reading: TimesheetReading) -> ApprovalKind:
    approval = reading.approval.value
    return approval.kind if approval else ApprovalKind.NONE


def _better(left: ReadField[T], right: ReadField[T]) -> ReadField[T]:
    """The more useful of two answers: a value beats a blank, surer beats less sure."""
    if left.value is None:
        return right
    if right.value is None:
        return left
    order = {Confidence.HIGH: 2, Confidence.MEDIUM: 1, Confidence.LOW: 0}
    return right if order[right.confidence] > order[left.confidence] else left


def combine_readings(
    readings: Sequence[TimesheetReading],
) -> tuple[TimesheetReading, list[Finding]]:
    """One reading from every attachment on one email (decision 24).

    A consultant working through their own firm sends the approved timesheet
    and the firm's invoice for the same hours in the same email. The timesheet
    is the proof of the hours and the approval; the invoice is where a printed
    total usually is. So the timesheet leads and the rest fill its gaps — and
    where the two disagree about who, which client, or which dates, nobody
    guesses: it goes to Kevin.
    """
    if not readings:
        raise ValueError("combine_readings needs at least one reading")
    if len(readings) == 1:
        return readings[0], []

    # The document that shows approval is the timesheet; it leads.
    with_approval = [r for r in readings if _kind_of(r) is not ApprovalKind.NONE]
    base = with_approval[0] if with_approval else readings[0]
    others = [r for r in readings if r is not base]

    findings: list[Finding] = []
    merged = base
    for other in others:
        for field_name in ("consultant_name", "client_name"):
            mine = getattr(merged, field_name).value
            theirs = getattr(other, field_name).value
            if mine is not None and theirs is not None and not names_match(mine, theirs):
                findings.append(
                    Finding(
                        ReviewCode.NOT_SURE,
                        f"The attachments on this email disagree about the {_plain(field_name)}:"
                        f' "{mine}" and "{theirs}".',
                    )
                )
        for field_name in ("period_start", "period_end"):
            mine = getattr(merged, field_name).value
            theirs = getattr(other, field_name).value
            if mine is not None and theirs is not None and mine != theirs:
                findings.append(
                    Finding(
                        ReviewCode.PERIOD_UNCLEAR,
                        "The attachments on this email disagree about the"
                        f" {_plain(field_name)}: {mine} and {theirs}.",
                    )
                )
        merged = merged.model_copy(
            update={
                name: _better(getattr(merged, name), getattr(other, name))
                for name in (
                    "consultant_name",
                    "client_name",
                    "end_client_name",
                    "period_start",
                    "period_end",
                    "stated_total_hours_hundredths",
                    "approval",
                )
            }
        )
        if not (merged.row_entries.value or []) and (other.row_entries.value or []):
            merged = merged.model_copy(update={"row_entries": other.row_entries})
        if not (merged.daily_entries.value or []) and (other.daily_entries.value or []):
            merged = merged.model_copy(update={"daily_entries": other.daily_entries})
        merged = merged.model_copy(
            update={"unusual_items": [*merged.unusual_items, *other.unusual_items]}
        )
    return merged, findings


def _plain(field_name: str) -> str:
    return {
        "consultant_name": "consultant",
        "client_name": "client",
        "period_start": "first date",
        "period_end": "last date",
    }[field_name]
