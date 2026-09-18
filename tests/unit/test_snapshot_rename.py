"""An item written before the bill rate joined the disagreement field.

`pay_disagreement` became `rate_disagreement` when the bill rate started using
it (docs/decisions.md #43). Items already in Kevin's database hold the old
name, and a rename that quietly dropped their text would lose the one sentence
saying what the disagreement was.
"""

from finance_ops_agent.domain.items import EngagementSnapshot

STORED = {
    "bill_rate_cents": 14_000,
    "pay_rate_cents": 10_000,
    "payment_terms_days": 30,
    "pay_timing_days": 15,
    "billing_emails": ["ap@acme.example"],
    "cc_emails": [],
    "payee": "Priya Shah",
    "paid_by": "bank transfer",
    "engagement_row_number": 2,
}


def test_an_item_stored_under_the_old_name_still_reads() -> None:
    snapshot = EngagementSnapshot.model_validate(
        {**STORED, "pay_disagreement": "QuickBooks pays $110.00 and the list says $100.00."}
    )
    assert "110.00" in snapshot.rate_disagreement


def test_the_new_name_is_what_gets_written_back() -> None:
    """One name in the database from here on, so the alias is only ever a
    reader of history."""
    snapshot = EngagementSnapshot.model_validate({**STORED, "pay_disagreement": "they differ"})
    written = snapshot.model_dump()
    assert written["rate_disagreement"] == "they differ"
    assert "pay_disagreement" not in written


def test_an_item_with_neither_is_not_a_disagreement() -> None:
    assert EngagementSnapshot.model_validate(STORED).rate_disagreement == ""
