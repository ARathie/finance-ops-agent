"""FOPS_RUN_EVERY_MINUTES, FOPS_BACKUP_AT, FOPS_HEARTBEAT_URL, FOPS_BACKUP_TARGET,
and what `fops doctor` says about the last two."""

from datetime import time
from pathlib import Path

import pytest

from finance_ops_agent.cli.doctor import CheckResult, check_backup_target, check_heartbeat
from finance_ops_agent.config import MissingSettingError, ServiceSettings

NAMES = ("FOPS_RUN_EVERY_MINUTES", "FOPS_BACKUP_AT", "FOPS_HEARTBEAT_URL", "FOPS_BACKUP_TARGET")


@pytest.fixture(autouse=True)
def clean(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in NAMES:
        monkeypatch.delenv(name, raising=False)


def test_the_defaults() -> None:
    settings = ServiceSettings.from_env()
    assert settings.run_every_minutes == 15
    assert settings.backup_at == time(2, 30)
    assert settings.heartbeat_url == ""
    assert settings.backup_target == ""


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("FOPS_RUN_EVERY_MINUTES", "often"),
        ("FOPS_RUN_EVERY_MINUTES", "0"),
        ("FOPS_BACKUP_AT", "half two"),
        ("FOPS_HEARTBEAT_URL", "http://hc-ping.example/abc"),
    ],
)
def test_a_bad_value_is_refused_by_name(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(MissingSettingError, match=name):
        ServiceSettings.from_env()


def test_doctor_heartbeat() -> None:
    assert check_heartbeat("").result is CheckResult.SKIP
    check = check_heartbeat("https://hc-ping.com/uuid")
    assert check.result is CheckResult.PASS
    assert "hc-ping.com" in check.detail


def test_doctor_backup_target(tmp_path: Path) -> None:
    assert check_backup_target("", lambda: []).result is CheckResult.SKIP
    found = check_backup_target(
        "b2:bucket", lambda: ["fops-backup-2026-10-28.zip", "fops-backup-2026-10-29.zip"]
    )
    assert found.result is CheckResult.PASS
    assert "2 backup(s)" in found.detail and "2026-10-29" in found.detail

    def unreachable() -> list[str]:
        raise OSError("bucket not found")

    failed = check_backup_target("b2:bucket", unreachable)
    assert failed.result is CheckResult.FAIL
    assert "bucket not found" in failed.detail


def test_serve_refuses_to_start_unconfigured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from finance_ops_agent.cli.main import main

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FOPS_TIMEZONE", raising=False)
    assert main(["serve"]) == 2
    assert "Not configured" in capsys.readouterr().out


def test_the_log_file_rotates_daily_and_keeps_thirty(tmp_path: Path) -> None:
    import logging.handlers

    from finance_ops_agent import logs

    logger = logs.configure(tmp_path / "fops.log")
    try:
        rotating = [
            handler
            for handler in logger.handlers
            if isinstance(handler, logging.handlers.TimedRotatingFileHandler)
        ]
        assert len(rotating) == 1
        assert rotating[0].when == "MIDNIGHT"
        assert rotating[0].backupCount == 30
    finally:
        for handler in logger.handlers:
            handler.close()
        logger.handlers.clear()
