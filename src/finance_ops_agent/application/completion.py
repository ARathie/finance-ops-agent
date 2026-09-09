"""When an item's period is fully covered and nothing waits on Kevin, sum the
hours, compute both amounts once, and make the item ready. Kevin's answered
"use N hours" replies override the summed hours."""

from datetime import date

from finance_ops_agent.application.context import RunDeps, RunReport
from finance_ops_agent.domain import checks
from finance_ops_agent.domain.items import Item, TimesheetRecord
from finance_ops_agent.domain.money import Hours, Money, invoice_amount, pay_amount
from finance_ops_agent.domain.reading import TimesheetReading
from finance_ops_agent.domain.statuses import ItemStatus


def stored_reading(record: TimesheetRecord) -> TimesheetReading:
    return TimesheetReading.model_validate(record.reading)


def reading_span(reading: TimesheetReading) -> tuple[date, date] | None:
    start, end = reading.period_start.value, reading.period_end.value
    if start is None or end is None or end < start:
        return None
    return start, end


def hours_override(deps: RunDeps, item: Item) -> Hours | None:
    """Kevin's answered "use N hours" reply, if any; the latest answer wins."""
    override: Hours | None = None
    for review in deps.store.reviews_for_item(item.id):
        if review.status != "answered":
            continue
        answer = deps.store.review_answer(review.id) or {}
        if answer.get("kind") == "hours" and answer.get("value"):
            try:
                override = Hours.parse(str(answer["value"]))
            except ValueError:
                continue
    return override


def complete_if_covered(deps: RunDeps, item: Item, report: RunReport) -> None:
    if item.status not in (ItemStatus.RECEIVED, ItemStatus.NEEDS_REVIEW):
        return
    if any(review.item_id == item.id for review in deps.store.open_reviews()):
        return  # something is still waiting on Kevin
    latest_by_span: dict[tuple[date, date], TimesheetReading] = {}
    for record in deps.store.timesheets_for_item(item.id):
        if record.is_duplicate or record.is_correction:
            continue
        reading = stored_reading(record)
        span = reading_span(reading)
        if span is not None:
            latest_by_span[span] = reading
    if not checks.period_fully_covered(item.period, list(latest_by_span)):
        report.note(
            f"waiting for the rest of the period: {item.consultant} at {item.client},"
            f" {item.period.start} to {item.period.end}"
        )
        return
    total = hours_override(deps, item)
    if total is None:
        total = Hours(0)
        for reading in latest_by_span.values():
            hours, _ = checks.check_hours(reading)
            total = total + Hours(hours or 0)
    billed = invoice_amount(total, Money(item.snapshot.bill_rate_cents))
    owed = pay_amount(total, Money(item.snapshot.pay_rate_cents))
    deps.store.set_item_amounts(item.id, total, billed, owed)
    if item.status is ItemStatus.NEEDS_REVIEW:
        deps.store.change_status(item.id, ItemStatus.RECEIVED, {"why": "answers applied"})
    deps.store.change_status(item.id, ItemStatus.READY, {"hours_hundredths": total.hundredths})
    report.items_made_ready += 1
    report.note(
        f"ready to invoice: {item.consultant} at {item.client},"
        f" {item.period.start} to {item.period.end}: {total} hours,"
        f" invoice {billed}, owed {owed}"
    )
