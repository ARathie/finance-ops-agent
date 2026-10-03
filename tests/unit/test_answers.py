"""Kevin's answered reviews are read in both shapes: one answer, or a list."""

from finance_ops_agent.application.answers import actions_in


def test_an_older_answer_is_one_action() -> None:
    assert actions_in({"kind": "hours", "value": "152"}) == [{"kind": "hours", "value": "152"}]


def test_a_reply_asking_for_several_things_keeps_them_all_in_order() -> None:
    answer: dict[str, object] = {
        "kind": "invoice_number",
        "value": "083126MT-MK-revised",
        "actions": [
            {"kind": "invoice_number", "value": "083126MT-MK-revised"},
            {"kind": "show_me_first", "value": None},
        ],
    }
    assert [action["kind"] for action in actions_in(answer)] == [
        "invoice_number",
        "show_me_first",
    ]


def test_no_answer_is_no_actions() -> None:
    assert actions_in(None) == []
    assert actions_in({}) == []
