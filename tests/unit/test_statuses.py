import pytest

from finance_ops_agent.domain.statuses import (
    ALLOWED_STATUS_CHANGES,
    INITIAL_STATUSES,
    DisallowedStatusChange,
    ItemStatus,
    change_status,
)

# The allowed changes, written out long-hand so a change to the table in
# statuses.py has to be made deliberately here too (and in status-tracking.md).
EXPECTED_ALLOWED: set[tuple[ItemStatus, ItemStatus]] = {
    (ItemStatus.WAITING_FOR_TIMESHEET, ItemStatus.RECEIVED),
    (ItemStatus.WAITING_FOR_TIMESHEET, ItemStatus.CANCELLED),
    (ItemStatus.RECEIVED, ItemStatus.NEEDS_REVIEW),
    (ItemStatus.RECEIVED, ItemStatus.READY),
    (ItemStatus.RECEIVED, ItemStatus.IGNORED),
    (ItemStatus.RECEIVED, ItemStatus.CANCELLED),
    (ItemStatus.NEEDS_REVIEW, ItemStatus.RECEIVED),
    (ItemStatus.NEEDS_REVIEW, ItemStatus.READY),
    (ItemStatus.NEEDS_REVIEW, ItemStatus.IGNORED),
    (ItemStatus.NEEDS_REVIEW, ItemStatus.CANCELLED),
    (ItemStatus.READY, ItemStatus.WAITING_FOR_APPROVAL),
    (ItemStatus.READY, ItemStatus.INVOICE_SENT),
    (ItemStatus.READY, ItemStatus.NEEDS_REVIEW),
    (ItemStatus.READY, ItemStatus.CANCELLED),
    (ItemStatus.WAITING_FOR_APPROVAL, ItemStatus.INVOICE_SENT),
    (ItemStatus.WAITING_FOR_APPROVAL, ItemStatus.NEEDS_REVIEW),
    (ItemStatus.WAITING_FOR_APPROVAL, ItemStatus.CANCELLED),
    (ItemStatus.INVOICE_SENT, ItemStatus.CLIENT_PAID),
    (ItemStatus.INVOICE_SENT, ItemStatus.NEEDS_REVIEW),
}

ALL_PAIRS = [(current, new) for current in ItemStatus for new in ItemStatus]


def test_statuses_match_the_docs() -> None:
    assert {status.value for status in ItemStatus} == {
        "waiting_for_timesheet",
        "received",
        "needs_review",
        "ready",
        "waiting_for_approval",
        "invoice_sent",
        "client_paid",
        "ignored",
        "cancelled",
    }


def test_every_status_has_an_entry_in_the_table() -> None:
    assert set(ALLOWED_STATUS_CHANGES) == set(ItemStatus)


def test_initial_statuses() -> None:
    assert {ItemStatus.WAITING_FOR_TIMESHEET, ItemStatus.RECEIVED} == INITIAL_STATUSES


@pytest.mark.parametrize(("current", "new"), sorted(EXPECTED_ALLOWED))
def test_every_allowed_change(current: ItemStatus, new: ItemStatus) -> None:
    assert change_status(current, new) is new


@pytest.mark.parametrize(
    ("current", "new"), [pair for pair in ALL_PAIRS if pair not in EXPECTED_ALLOWED]
)
def test_every_disallowed_change_raises(current: ItemStatus, new: ItemStatus) -> None:
    with pytest.raises(DisallowedStatusChange):
        change_status(current, new)


@pytest.mark.parametrize(
    "terminal", [ItemStatus.CLIENT_PAID, ItemStatus.IGNORED, ItemStatus.CANCELLED]
)
def test_terminal_statuses_allow_nothing(terminal: ItemStatus) -> None:
    assert ALLOWED_STATUS_CHANGES[terminal] == frozenset()
