"""The investigator's tools: read-only, described for a model, never raising."""

import json
from datetime import date

from finance_ops_agent.application.agent_tools import ReadOnlyToolbox
from finance_ops_agent.application.diagnosis import Looking
from finance_ops_agent.application.investigation import picked_option
from finance_ops_agent.ports.accounting import AccountingFailed
from tests.scenarios.conftest import PRIYA, ScenarioEnv, reading
from tests.scenarios.test_diagnosis import ReadOnlyAccounting, ReadOnlyStore

AUG = (date(2026, 8, 1), date(2026, 8, 31))


def toolbox(env: ScenarioEnv) -> ReadOnlyToolbox:
    return ReadOnlyToolbox(
        Looking(store=ReadOnlyStore(env.store), accounting=ReadOnlyAccounting(env.accounting))
    )


def test_every_tool_has_a_strict_shaped_schema(env: ScenarioEnv) -> None:
    specs = toolbox(env).specs()
    names = [spec.name for spec in specs]
    assert names == [
        "describe_item",
        "explain_invoice_number",
        "check_recorded_invoices",
        "list_items",
        "list_open_reviews",
    ]
    for spec in specs:
        schema = spec.input_schema
        assert schema["additionalProperties"] is False
        assert sorted(schema["required"]) == sorted(schema["properties"])


def test_the_inbox_and_items_tools_appear_only_when_there_is_something_to_look_at(
    env: ScenarioEnv,
) -> None:
    from finance_ops_agent.domain.engagements import parse_workbook

    full = ReadOnlyToolbox(
        Looking(
            store=env.store,
            accounting=env.accounting,
            inbox=env.mailbox,
            workbook=parse_workbook(env.workbook),
        )
    )
    assert {"check_inbox", "check_items"} <= {spec.name for spec in full.specs()}


def test_describe_item_answers_with_facts(env: ScenarioEnv) -> None:
    env.add_email(PRIYA, scripted_reading=reading(*AUG))
    env.run()

    text, ok = toolbox(env).call("describe_item", {"item_id": env.the_item().id})

    assert ok
    answer = json.loads(text)
    assert answer["item_id"] == env.the_item().id
    assert any("Priya Shah at Acme Corp" in fact for fact in answer["facts"])


def test_list_items_filters_by_status(env: ScenarioEnv) -> None:
    env.add_email(PRIYA, scripted_reading=reading(*AUG))
    env.run()
    box = toolbox(env)

    ready = json.loads(box.call("list_items", {"status": "ready"})[0])["items"]
    paid = json.loads(box.call("list_items", {"status": "client_paid"})[0])["items"]
    every = json.loads(box.call("list_items", {"status": "any"})[0])["items"]

    assert [item["status"] for item in ready] == ["ready"]
    assert paid == []
    assert len(every) == 1


def test_a_failing_tool_says_why_instead_of_raising(env: ScenarioEnv) -> None:
    box = toolbox(env)

    assert box.call("no_such_tool", {}) == (
        json.dumps({"error": "there is no tool called no_such_tool"}),
        False,
    )
    text, ok = box.call("describe_item", {"item_id": 999})
    assert not ok and "no item 999" in text
    text, ok = box.call("describe_item", {})
    assert not ok and "bad arguments" in text
    env.accounting.fail_with = AccountingFailed("timed out")
    text, ok = box.call("explain_invoice_number", {"number": "X", "item_id": None})
    assert ok  # the diagnosis reports the outage as a finding
    assert "accounting_unreachable" in text


class TestPickingAnOption:
    OFFERED = ["try again", "use 083126MT-MK-revised"]

    def test_letters_numbers_and_short_phrases(self) -> None:
        for reply, words in [
            ("A", "try again"),
            ("b.", "use 083126MT-MK-revised"),
            ("Option 2", "use 083126MT-MK-revised"),
            ("go with A please", "try again"),
            ("(B)", "use 083126MT-MK-revised"),
        ]:
            assert picked_option(reply, self.OFFERED) == words, reply

    def test_anything_more_is_left_to_the_reader(self) -> None:
        for reply in ["A, and send it to me first", "thanks", "C", "I deleted it", ""]:
            assert picked_option(reply, self.OFFERED) is None, reply

    def test_nothing_offered_means_nothing_picked(self) -> None:
        assert picked_option("A", []) is None


def test_no_amount_of_money_reaches_the_model(env: ScenarioEnv) -> None:
    """Rates and amounts are never sent to the model; the tools mask them."""
    env.add_email(PRIYA, scripted_reading=reading(*AUG))
    env.run()
    assert env.the_item().invoice_amount is not None

    text, ok = toolbox(env).call("describe_item", {"item_id": env.the_item().id})

    assert ok
    assert "$[amount]" in text
    assert "21,840" not in text and "140" not in text
