"""The checks a timesheet goes through, as pure functions with no I/O.

Each returns what it found plus Findings (a review code and the message for
Kevin). The application runs them in the order from docs/timesheet-checks.md
and sends one review email listing everything wrong with a timesheet.
"""

import re
from dataclasses import dataclass
from datetime import date, timedelta

from finance_ops_agent.domain.engagements import Consultant, Engagement
from finance_ops_agent.domain.periods import BillingPeriod, period_containing
from finance_ops_agent.domain.reading import ApprovalKind, Confidence, TimesheetReading
from finance_ops_agent.domain.review import REVIEW_MESSAGES, ReviewCode

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


def fit_billing_period(
    row: Engagement, reading: TimesheetReading
) -> tuple[BillingPeriod | None, list[Finding]]:
    """The timesheet's dates must fit inside one period on the schedule."""
    start, end = reading.period_start.value, reading.period_end.value
    if start is None or end is None or end < start:
        return None, [Finding(ReviewCode.PERIOD_UNCLEAR, "I can't tell which dates this covers.")]
    mismatch = Finding(
        ReviewCode.PERIOD_MISMATCH,
        "The dates don't line up with the billing schedule for this engagement.",
    )
    try:
        period = period_containing(
            start,
            row.billing_schedule,
            row.start_date,
            row.end_date,
            row.first_period_start,
        )
    except ValueError:
        return None, [mismatch]
    if not period.covers(end):
        return None, [mismatch]
    return period, []


def _weekdays(start: date, end: date) -> int:
    count, day = 0, start
    while day <= end:
        if day.weekday() < 5:
            count += 1
        day += timedelta(days=1)
    return count


def check_hours(reading: TimesheetReading) -> tuple[int | None, list[Finding]]:
    """The hours to invoice for this timesheet's own dates (hundredths), plus
    anything wrong with them. The printed total is what gets invoiced."""
    stated = reading.stated_total_hours_hundredths.value
    summed = reading.summed_daily_hundredths()
    rows = reading.summed_row_hundredths()
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
    findings: list[Finding] = []
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
