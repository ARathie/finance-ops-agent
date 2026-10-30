"""`fops serve` asked to stop mid-run: the run ends at a safe point, leaves
nothing in flight, and the next run carries on without doing anything twice."""

from datetime import date

import pytest

from finance_ops_agent.application.context import RunStopped
from finance_ops_agent.application.run import run_once
from tests.scenarios.conftest import PRIYA, ScenarioEnv, reading

AUG_START, AUG_END = date(2026, 8, 1), date(2026, 8, 31)


def in_flight(env: ScenarioEnv) -> list[str]:
    return [r.idempotency_key for r in env.store.outgoing_records() if r.status == "in_flight"]


def test_a_stop_between_emails_leaves_nothing_half_sent(env: ScenarioEnv) -> None:
    env.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END))
    deps = env.deps()
    deps.stop_requested = lambda: len(env.sender.sent_emails()) >= 1

    with pytest.raises(RunStopped):
        run_once(deps)

    assert len(env.sender.sent_emails()) == 1
    assert in_flight(env) == []
    pending = [r for r in env.store.outgoing_records() if r.status == "pending"]
    assert pending, "the rest waits for the next run"

    env.run()
    subjects = env.sent_subjects()
    assert len(subjects) == len(set(subjects)), "nothing is sent twice"
    assert [r for r in env.store.outgoing_records() if r.status == "pending"] == []
    assert in_flight(env) == []


def test_a_stop_between_messages_leaves_the_rest_for_the_next_run(env: ScenarioEnv) -> None:
    env.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END))
    env.add_email("stranger@example.com", subject="Hello", attachment=None)
    deps = env.deps()
    deps.stop_requested = lambda: len(env.store.unprocessed_messages()) < 2

    with pytest.raises(RunStopped):
        run_once(deps)

    assert len(env.store.unprocessed_messages()) == 1
    assert env.sender.sent_emails() == []
    assert in_flight(env) == []

    env.run()
    assert env.store.unprocessed_messages() == []
    assert len(env.store.list_items()) == 1


def test_a_stop_asked_before_the_run_only_stores_the_mail(env: ScenarioEnv) -> None:
    """Storing is safe to stop after: the position moves only once it is done."""
    env.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END))
    deps = env.deps()
    deps.stop_requested = lambda: True

    with pytest.raises(RunStopped):
        run_once(deps)

    assert len(env.store.unprocessed_messages()) == 1
    assert env.store.outgoing_records() == []
    assert env.sender.sent_emails() == []
