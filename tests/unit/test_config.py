"""The settings from the environment, including the ones with no safe default."""

from datetime import date
from pathlib import Path

import pytest

from finance_ops_agent.application.context import Mode
from finance_ops_agent.config import Config, MailSettings, MissingSettingError

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
        "MAIL_IMAP_HOST",
        "MAIL_IMAP_PORT",
        "MAIL_IMAP_SECURITY",
        "MAIL_SMTP_HOST",
        "MAIL_SMTP_PORT",
        "MAIL_SMTP_SECURITY",
        "MAIL_USERNAME",
        "MAIL_PASSWORD",
        "MAIL_START_DATE",
        "MAIL_FOLDER_PREFIX",
        "MAIL_SENT_FOLDER",
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


def test_mail_settings_need_a_username_and_password(monkeypatch: pytest.MonkeyPatch) -> None:
    set_env(monkeypatch)
    with pytest.raises(MissingSettingError, match="MAIL_USERNAME"):
        MailSettings.from_env()
    monkeypatch.setenv("MAIL_USERNAME", "jay@icon-technologies.com")
    with pytest.raises(MissingSettingError, match="MAIL_PASSWORD"):
        MailSettings.from_env()
    monkeypatch.setenv("MAIL_PASSWORD", "not-a-real-password")
    settings = MailSettings.from_env()
    # Rackspace Email defaults from docs/integrations/email-imap-smtp.md.
    assert (settings.imap_host, settings.imap_port, settings.imap_security) == (
        "secure.emailsrvr.com",
        993,
        "ssl",
    )
    assert (settings.smtp_host, settings.smtp_port, settings.smtp_security) == (
        "secure.emailsrvr.com",
        465,
        "ssl",
    )
    assert settings.start_date is None
    assert settings.folder_prefix == "Agent"
    assert settings.sent_folder is None


def _mail_env(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> None:
    set_env(monkeypatch, MAIL_USERNAME="jay@icon-technologies.com", MAIL_PASSWORD="pw", **overrides)


def test_the_start_date_is_a_date(monkeypatch: pytest.MonkeyPatch) -> None:
    _mail_env(monkeypatch, MAIL_START_DATE="2026-09-09")
    assert MailSettings.from_env().start_date == date(2026, 9, 9)
    _mail_env(monkeypatch, MAIL_START_DATE="Sept 9")
    with pytest.raises(MissingSettingError, match="MAIL_START_DATE"):
        MailSettings.from_env()


def test_ports_must_be_numbers(monkeypatch: pytest.MonkeyPatch) -> None:
    _mail_env(monkeypatch, MAIL_IMAP_PORT="nine-nine-three")
    with pytest.raises(MissingSettingError, match="MAIL_IMAP_PORT"):
        MailSettings.from_env()
    _mail_env(monkeypatch, MAIL_SMTP_PORT="587", MAIL_SMTP_SECURITY="starttls")
    settings = MailSettings.from_env()
    assert (settings.smtp_port, settings.smtp_security) == (587, "starttls")


def test_security_choices_are_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    _mail_env(monkeypatch, MAIL_IMAP_SECURITY="plain")
    with pytest.raises(MissingSettingError, match="MAIL_IMAP_SECURITY"):
        MailSettings.from_env()


def test_no_encryption_is_only_allowed_for_a_local_test_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A real mail server always gets TLS; "none" exists only for the test server in CI.
    _mail_env(monkeypatch, MAIL_SMTP_SECURITY="none")
    with pytest.raises(MissingSettingError, match="secure.emailsrvr.com"):
        MailSettings.from_env()
    _mail_env(
        monkeypatch,
        MAIL_IMAP_HOST="127.0.0.1",
        MAIL_IMAP_SECURITY="none",
        MAIL_SMTP_HOST="localhost",
        MAIL_SMTP_SECURITY="none",
    )
    settings = MailSettings.from_env()
    assert (settings.imap_security, settings.smtp_security) == ("none", "none")


def test_folder_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    _mail_env(monkeypatch, MAIL_FOLDER_PREFIX="  ", MAIL_SENT_FOLDER="Sent Items")
    settings = MailSettings.from_env()
    assert settings.folder_prefix == "Agent"  # blank means the default
    assert settings.sent_folder == "Sent Items"
