"""A failed QuickBooks check says where the thing to change lives.

`docs/quickbooks-setup.md` is the other half of these checks: they are what the
agent expects, it is what Kevin clicks. A failure that does not point at it
leaves him with a correct sentence and nowhere to go.
"""

from finance_ops_agent.cli.doctor import SETUP_DOC, Check, CheckResult, setup_pointer


def _failed(name: str) -> Check:
    return Check(name, CheckResult.FAIL, "something is wrong")


def test_a_failed_quickbooks_check_points_at_the_setup_document() -> None:
    pointer = setup_pointer([_failed("quickbooks products")])
    assert pointer is not None
    assert SETUP_DOC in pointer


def test_it_is_said_once_however_many_checks_failed() -> None:
    pointer = setup_pointer(
        [
            _failed("quickbooks customers"),
            _failed("quickbooks products"),
            _failed("quickbooks pay rates"),
        ]
    )
    assert pointer is not None
    assert pointer.count(SETUP_DOC) == 1


def test_a_failure_with_nothing_to_do_with_quickbooks_gets_no_pointer() -> None:
    """The mailbox and the engagement list are set up somewhere else entirely."""
    assert setup_pointer([_failed("read the mailbox (imap)"), _failed("engagement list")]) is None


def test_nothing_failed_means_nothing_to_point_at() -> None:
    assert setup_pointer([]) is None


def test_the_document_it_names_is_in_the_repository() -> None:
    """A pointer at a file that moved is worse than no pointer."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    assert (root / SETUP_DOC).is_file()
