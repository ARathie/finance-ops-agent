"""Backups off the machine: uploaded through rclone, kept 90 days, the first of
each month for good (roadmap PR 14). A temporary folder is the rclone remote,
so this runs the real rclone without any bucket."""

import os
import shutil
import zipfile
from datetime import date
from pathlib import Path

import pytest

from finance_ops_agent.adapters.service.backup_target import BackupTargetFailed, RcloneTarget
from finance_ops_agent.application.backup import (
    back_up,
    backup_day,
    backup_name,
    backups_to_delete,
    local_backups_to_delete,
)

TODAY = date(2026, 10, 29)

needs_rclone = pytest.mark.skipif(
    shutil.which("rclone") is None and not os.environ.get("CI"),
    reason="rclone is not installed here (CI installs it)",
)


class TestRetention:
    def test_ninety_days_of_dailies_and_every_first_of_the_month(self) -> None:
        names = [
            backup_name(date(2026, 7, 31)),  # 90 days: kept
            backup_name(date(2026, 7, 30)),  # 91 days: goes
            backup_name(date(2026, 7, 1)),  # a first of the month: kept for good
            backup_name(date(2024, 1, 1)),
            backup_name(date(2025, 3, 17)),  # goes
            backup_name(TODAY),
        ]
        assert backups_to_delete(names, TODAY) == [
            "fops-backup-2025-03-17.zip",
            "fops-backup-2026-07-30.zip",
        ]

    def test_anything_not_named_like_a_backup_is_never_touched(self) -> None:
        names = ["notes.txt", "fops-backup-2020-02-30.zip", "fops-backup-2020-02-03.zip.part"]
        assert backups_to_delete(names, TODAY) == []
        assert backup_day("fops-backup-2020-02-30.zip") is None

    def test_the_machine_keeps_the_last_week(self) -> None:
        names = [backup_name(date(2026, 10, day)) for day in range(1, 11)] + ["other.zip"]
        assert local_backups_to_delete(names) == [
            backup_name(date(2026, 10, day)) for day in (1, 2, 3)
        ]
        assert local_backups_to_delete(names[:3]) == []


def test_backups_in_the_data_folder_are_not_zipped_into_the_next(tmp_path: Path) -> None:
    data = tmp_path / "data"
    (data / "files").mkdir(parents=True)
    (data / "files" / "a.pdf").write_bytes(b"%PDF")
    first = back_up(data, data / "backups", date(2026, 10, 28))
    second = back_up(data, data / "backups", TODAY)
    assert first.files == second.files == 1
    with zipfile.ZipFile(second.path) as archive:
        assert archive.namelist() == ["files/a.pdf"]


@needs_rclone
class TestRcloneTarget:
    def test_upload_list_and_delete_against_a_folder_remote(self, tmp_path: Path) -> None:
        bucket = tmp_path / "bucket"
        bucket.mkdir()
        target = RcloneTarget(str(bucket))
        for day in (date(2026, 6, 1), date(2026, 6, 2), TODAY):
            archive = tmp_path / backup_name(day)
            archive.write_bytes(b"zip")
            target.upload(archive)
        assert sorted(target.names()) == [
            "fops-backup-2026-06-01.zip",
            "fops-backup-2026-06-02.zip",
            "fops-backup-2026-10-29.zip",
        ]
        for name in backups_to_delete(target.names(), TODAY):
            target.delete(name)
        assert sorted(path.name for path in bucket.iterdir()) == [
            "fops-backup-2026-06-01.zip",
            "fops-backup-2026-10-29.zip",
        ]

    def test_an_unreachable_target_says_so(self, tmp_path: Path) -> None:
        with pytest.raises(BackupTargetFailed, match="lsf failed"):
            RcloneTarget(str(tmp_path / "missing")).names()


def test_no_rclone_is_a_plain_failure(tmp_path: Path) -> None:
    with pytest.raises(BackupTargetFailed, match="not installed"):
        RcloneTarget(str(tmp_path), rclone="no-such-rclone").names()
