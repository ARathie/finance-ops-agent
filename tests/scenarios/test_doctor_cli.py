"""`fops doctor` and `fops run` refuse to guess when the environment is unset."""

import pytest

from finance_ops_agent.cli.main import main

REQUIRED = (
    "FOPS_TIMEZONE",
    "FOPS_ENGAGEMENT_LIST",
    "FOPS_ADMIN_EMAIL",
    "FOPS_AGENT_MAILBOX",
    "MS_TENANT_ID",
    "MS_CLIENT_ID",
    "MS_CLIENT_SECRET",
    "FOPS_MODE",
    "FOPS_DATA_DIR",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in REQUIRED:
        monkeypatch.delenv(name, raising=False)


def test_doctor_with_nothing_configured_says_what_is_missing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["doctor"]) == 1
    out = capsys.readouterr().out
    assert "FOPS_TIMEZONE" in out
    assert "FAIL" in out


def test_doctor_names_the_mailbox_settings_it_still_needs(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: object
) -> None:
    monkeypatch.setenv("FOPS_TIMEZONE", "America/New_York")
    monkeypatch.setenv("FOPS_ENGAGEMENT_LIST", "/nowhere/engagements.xlsx")
    monkeypatch.setenv("FOPS_ADMIN_EMAIL", "kevin@icon-technologies.com")
    monkeypatch.setenv("FOPS_AGENT_MAILBOX", "jay@icon-technologies.com")
    monkeypatch.setenv("FOPS_DATA_DIR", str(tmp_path))

    assert main(["doctor"]) == 1
    out = capsys.readouterr().out
    assert "MS_TENANT_ID" in out
    # It got far enough to check the settings and the engagement list.
    assert "mode dry_run" in out
    assert "engagement list" in out
    # And it never claims to have sent anything.
    assert "Nothing was sent to a client." in out


def test_run_without_configuration_stops_before_touching_anything(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["run"]) == 2
    assert "Not configured" in capsys.readouterr().out


def test_dry_run_without_fake_is_a_real_dry_run_and_needs_configuration(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["dry-run"]) == 2
    assert "Not configured" in capsys.readouterr().out
