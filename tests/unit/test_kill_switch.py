"""`FOPS_MODE=dry_run` overrides everything (docs/decisions.md #10).

Dry run is the kill switch: while the setting says dry run, nothing a command
line can say makes the agent send anything to a client or create anything in
QuickBooks. A flag may still lower a live mode to dry run, which is always safe.
"""

from pathlib import Path

import pytest

from finance_ops_agent.application.context import Mode, effective_mode
from finance_ops_agent.cli.main import _real_deps

LIVE_MODES = [Mode.ASK_FIRST, Mode.AUTO]


@pytest.mark.parametrize("requested", [None, *list(Mode)])
def test_the_setting_wins_while_it_says_dry_run(requested: Mode | None) -> None:
    assert effective_mode(Mode.DRY_RUN, requested) is Mode.DRY_RUN


@pytest.mark.parametrize("configured", LIVE_MODES)
def test_a_flag_can_always_lower_a_live_mode_to_dry_run(configured: Mode) -> None:
    assert effective_mode(configured, Mode.DRY_RUN) is Mode.DRY_RUN


@pytest.mark.parametrize("configured", LIVE_MODES)
def test_without_a_flag_the_setting_is_used(configured: Mode) -> None:
    assert effective_mode(configured, None) is configured


def test_a_flag_may_move_between_the_two_live_modes() -> None:
    assert effective_mode(Mode.ASK_FIRST, Mode.AUTO) is Mode.AUTO
    assert effective_mode(Mode.AUTO, Mode.ASK_FIRST) is Mode.ASK_FIRST


def real_environment(monkeypatch: pytest.MonkeyPatch, data_dir: Path, mode: str) -> None:
    """Enough settings for the real wiring; no credential here reaches a network."""
    for name, value in {
        "FOPS_MODE": mode,
        "FOPS_TIMEZONE": "America/New_York",
        "FOPS_ENGAGEMENT_LIST": str(data_dir / "engagements.xlsx"),
        "FOPS_ADMIN_EMAIL": "kevin@icon-technologies.com",
        "FOPS_AGENT_MAILBOX": "jay@icon-technologies.com",
        "FOPS_DATA_DIR": str(data_dir),
        "FOPS_ACCOUNTING": "manual",
        "MS_TENANT_ID": "not-a-real-tenant",
        "MS_CLIENT_ID": "not-a-real-client",
        "MS_CLIENT_SECRET": "not-a-real-secret",
        "ANTHROPIC_API_KEY": "not-a-real-key",
    }.items():
        monkeypatch.setenv(name, value)


@pytest.mark.parametrize("asked_for", ["auto", "ask_first"])
def test_the_real_run_is_dry_run_however_it_is_asked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, asked_for: str
) -> None:
    """The whole real wiring, from the environment, with a flag asking to send."""
    real_environment(monkeypatch, tmp_path, "dry_run")
    deps = _real_deps(Mode(asked_for))
    assert deps.settings.mode is Mode.DRY_RUN


def test_the_flag_still_works_when_the_setting_allows_sending(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    real_environment(monkeypatch, tmp_path, "auto")
    assert _real_deps(Mode.ASK_FIRST).settings.mode is Mode.ASK_FIRST
    assert _real_deps(None).settings.mode is Mode.AUTO
