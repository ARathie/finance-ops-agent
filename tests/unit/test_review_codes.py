from finance_ops_agent.domain.review import REVIEW_MESSAGES, ReviewCode


def test_codes_match_the_docs() -> None:
    assert {code.value for code in ReviewCode} == {
        "UNKNOWN_SENDER",
        "NO_ATTACHMENT",
        "CANT_READ_ATTACHMENT",
        "CONSULTANT_UNKNOWN",
        "ENGAGEMENT_UNCLEAR",
        "PERIOD_UNCLEAR",
        "PERIOD_MISMATCH",
        "HOURS_MISSING",
        "HOURS_DONT_ADD_UP",
        "HOURS_UNUSUAL",
        "NO_APPROVAL",
        "NOT_SURE",
        "RATE_MISSING",
        "NO_BILLING_CONTACT",
        "LIST_ROW_PROBLEM",
        "CORRECTION",
        "SEND_FAILED",
        "SEND_UNCERTAIN",
        "QUICKBOOKS_FAILED",
        "MAILBOX_PROBLEM",
        "QUICKBOOKS_RECONNECT",
    }


def test_every_code_has_a_message_for_kevin() -> None:
    assert set(REVIEW_MESSAGES) == set(ReviewCode)
    assert all(message for message in REVIEW_MESSAGES.values())
