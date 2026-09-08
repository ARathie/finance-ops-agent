"""The statuses a timesheet item can have and the changes allowed between them.

The statuses and their meanings are defined in docs/status-tracking.md, which also
lists these allowed changes in plain words. Do not add statuses here; add them
there first (see CLAUDE.md).
"""

from enum import StrEnum


class ItemStatus(StrEnum):
    WAITING_FOR_TIMESHEET = "waiting_for_timesheet"
    RECEIVED = "received"
    NEEDS_REVIEW = "needs_review"
    READY = "ready"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    INVOICE_SENT = "invoice_sent"
    CLIENT_PAID = "client_paid"
    IGNORED = "ignored"
    CANCELLED = "cancelled"


class DisallowedStatusChange(Exception):
    def __init__(self, current: ItemStatus, new: ItemStatus) -> None:
        super().__init__(f"a timesheet item cannot go from {current} to {new}")
        self.current = current
        self.new = new


# An item is created as waiting_for_timesheet (the period ended, nothing arrived)
# or received (a timesheet arrived first).
INITIAL_STATUSES = frozenset({ItemStatus.WAITING_FOR_TIMESHEET, ItemStatus.RECEIVED})

ALLOWED_STATUS_CHANGES: dict[ItemStatus, frozenset[ItemStatus]] = {
    ItemStatus.WAITING_FOR_TIMESHEET: frozenset(
        {
            ItemStatus.RECEIVED,
            ItemStatus.CANCELLED,
        }
    ),
    ItemStatus.RECEIVED: frozenset(
        {
            ItemStatus.NEEDS_REVIEW,
            ItemStatus.READY,
            ItemStatus.IGNORED,
            ItemStatus.CANCELLED,
        }
    ),
    ItemStatus.NEEDS_REVIEW: frozenset(
        {
            ItemStatus.RECEIVED,
            ItemStatus.READY,
            ItemStatus.IGNORED,
            ItemStatus.CANCELLED,
        }
    ),
    ItemStatus.READY: frozenset(
        {
            ItemStatus.WAITING_FOR_APPROVAL,
            ItemStatus.INVOICE_SENT,
            ItemStatus.NEEDS_REVIEW,
            ItemStatus.CANCELLED,
        }
    ),
    ItemStatus.WAITING_FOR_APPROVAL: frozenset(
        {
            ItemStatus.INVOICE_SENT,
            ItemStatus.NEEDS_REVIEW,
            ItemStatus.CANCELLED,
        }
    ),
    ItemStatus.INVOICE_SENT: frozenset(
        {
            ItemStatus.CLIENT_PAID,
            ItemStatus.NEEDS_REVIEW,
        }
    ),
    ItemStatus.CLIENT_PAID: frozenset(),
    ItemStatus.IGNORED: frozenset(),
    ItemStatus.CANCELLED: frozenset(),
}


def change_status(current: ItemStatus, new: ItemStatus) -> ItemStatus:
    """Return the new status, or raise DisallowedStatusChange."""
    if new not in ALLOWED_STATUS_CHANGES[current]:
        raise DisallowedStatusChange(current, new)
    return new
