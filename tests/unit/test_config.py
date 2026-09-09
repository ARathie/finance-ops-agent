"""The settings from the environment, including the ones with no safe default."""

from pathlib import Path

import pytest

from finance_ops_agent.application.context import Mode
from finance_ops_agent.config import Config, MicrosoftSettings, MissingSettingError

FULL_ENV = {
    "FOPS_TIMEZONE": "America/New_York",
    "FOPS_ENGAGEMENT_LIST": "/data/engagements.xlsx",
    "FOPS_ADMIN_EMAIL": "kevin@icon-technologies.com",
    "FOPS_AGENT_MAILBOX": "jay@icon-technologies.com",
}


def set_env(monkeypatch: pytest.MonkeyPatch, **overrides: str | None) -> None:
    for name in (
        *FULL_ENV,
        "FOPS_MODE",
        "FOPS_DATA_DIR",
        "FOPS_MODEL",
        "FOPS_ACCOUNTING",
        "MS_TENANT_ID",
        "MS_CLIENT_ID",
        "MS_CLIENT_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    for name, value in {**FULL_ENV, **overrides}.items():
        if value is not None:
            monkeypatch.setenv(name, value)


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    set_env(monkeypatch)
    config = Config.from_env()
    assert config.mode is Mode.DRY_RUN  # the safe default
    assert config.data_dir == Path("data")
    assert config.model == "claude-opus-5"
    assert config.accounting == "manual"


def test_the_timezone_has_no_default(monkeypatch: pytest.MonkeyPatch) -> None:
    set_env(monkeypatch, FOPS_TIMEZONE=None)
    with pytest.raises(MissingSettingError, match="FOPS_TIMEZONE"):
        Config.from_env()


@pytest.mark.parametrize(
    "missing", ["FOPS_ENGAGEMENT_LIST", "FOPS_ADMIN_EMAIL", "FOPS_AGENT_MAILBOX"]
)
def test_every_required_setting_is_required(monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    set_env(monkeypatch, **{missing: None})
    with pytest.raises(MissingSettingError, match=missing):
        Config.from_env()


def test_an_unknown_mode_is_refused_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    set_env(monkeypatch, FOPS_MODE="send_everything")
    with pytest.raises(MissingSettingError, match="dry_run"):
        Config.from_env()


@pytest.mark.parametrize("mode", list(Mode))
def test_every_mode_parses(monkeypatch: pytest.MonkeyPatch, mode: Mode) -> None:
    set_env(monkeypatch, FOPS_MODE=mode.value)
    assert Config.from_env().mode is mode


def test_microsoft_settings_need_all_four(monkeypatch: pytest.MonkeyPatch) -> None:
    set_env(monkeypatch)
    with pytest.raises(MissingSettingError, match="MS_TENANT_ID"):
        MicrosoftSettings.from_env()
    monkeypatch.setenv("MS_TENANT_ID", "tenant")
    monkeypatch.setenv("MS_CLIENT_ID", "client")
    monkeypatch.setenv("MS_CLIENT_SECRET", "secret")
    settings = MicrosoftSettings.from_env()
    assert settings.mailbox == "jay@icon-technologies.com"
