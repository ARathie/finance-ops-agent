"""The doctor proves the credentials that read timesheets.

The check asks the API to describe the configured model, which costs nothing
and reads nothing. These tests never reach the network: they either pass their
own `describe`, or leave the credentials unset so the check stops before it
would make a call.
"""

import pytest

from finance_ops_agent.cli.doctor import CheckResult, check_claude_api


def test_it_passes_and_names_the_model() -> None:
    check = check_claude_api("claude-opus-5", lambda model: f"Claude Opus 5 ({model}) answers")
    assert check.result is CheckResult.PASS
    assert "claude-opus-5" in check.detail


def test_a_refused_key_fails_and_says_where_to_fix_it(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(model: str) -> str:
        raise RuntimeError("the key was refused; check ANTHROPIC_API_KEY in .env")

    check = check_claude_api("claude-opus-5", refuse)
    assert check.result is CheckResult.FAIL
    assert "ANTHROPIC_API_KEY" in check.detail


def test_with_no_credentials_it_fails_without_calling_the_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No key means no network call: the check says so and stops."""
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.delenv(name, raising=False)

    def explode(*args: object, **kwargs: object) -> object:
        raise AssertionError("the doctor must not reach the network without credentials")

    monkeypatch.setattr("anthropic.Anthropic", explode)

    check = check_claude_api("claude-opus-5")
    assert check.result is CheckResult.FAIL
    assert "ANTHROPIC_API_KEY is not set" in check.detail


def test_the_doctor_never_crashes_on_an_unexpected_error() -> None:
    def boom(model: str) -> str:
        raise ValueError("something else went wrong")

    assert check_claude_api("claude-opus-5", boom).result is CheckResult.FAIL
