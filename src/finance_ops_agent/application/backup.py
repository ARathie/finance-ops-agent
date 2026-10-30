"""`fops backup` and `fops restore`: the data folder in one zip.

Everything the agent cannot rebuild lives under data/ - the database, the
stored attachments and PDFs, the QuickBooks tokens, the mailbox cursor. A
backup is worth nothing until a restore has been tried, so restore is a real
command with a real test, not a note in a runbook.

The lock file is deliberately left out: it belongs to a running process, not to
the data. So are earlier backups, when they are kept inside the data folder
(`data/backups`, the default): otherwise every zip would hold all the ones
before it, and the one being written would be zipped into itself.

Off the machine, `fops serve` keeps every daily backup for 90 days and the
first of each month for good, because these are financial records
(`backups_to_delete`). On the machine it keeps the last week, which is enough
to put something back quickly; the bucket is the real copy.
"""

import re
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from finance_ops_agent.ports.store import Store

SKIPPED_NAMES = frozenset({"run.lock"})
# The sidecar files belong to a live connection. They are safe to leave out
# only because back_up checkpoints the database first.
SKIPPED_SUFFIXES = (".db-wal", ".db-shm")

BACKUP_NAME = re.compile(r"^fops-backup-(\d{4}-\d{2}-\d{2})\.zip$")
KEEP_DAILY = timedelta(days=90)
KEEP_ON_THE_MACHINE = 7


def backup_name(day: date) -> str:
    return f"fops-backup-{day.isoformat()}.zip"


def backup_day(name: str) -> date | None:
    """The day a backup file is for, or None for anything that is not one."""
    match = BACKUP_NAME.match(name)
    if match is None:
        return None
    try:
        return date.fromisoformat(match[1])
    except ValueError:
        return None


def backups_to_delete(names: Iterable[str], today: date) -> list[str]:
    """The bucket's retention: daily backups for 90 days, the first of each
    month for good. Anything not named like a backup is never touched."""
    doomed = []
    for name in names:
        day = backup_day(name)
        if day is None or day.day == 1:
            continue
        if today - day > KEEP_DAILY:
            doomed.append(name)
    return sorted(doomed)


def local_backups_to_delete(names: Iterable[str], keep: int = KEEP_ON_THE_MACHINE) -> list[str]:
    """All but the newest `keep` backups in a folder on the machine."""
    dated = sorted((day, name) for name in names if (day := backup_day(name)) is not None)
    return [name for _, name in dated[: max(len(dated) - keep, 0)]]


class RestoreRefused(Exception):
    """The restore would overwrite a data folder that is not empty."""


@dataclass(frozen=True)
class BackupResult:
    path: Path
    files: int


def _should_include(path: Path) -> bool:
    if path.name in SKIPPED_NAMES:
        return False
    return not path.name.endswith(SKIPPED_SUFFIXES)


def back_up(
    data_dir: Path,
    destination_dir: Path,
    today: date,
    store: "Store | None" = None,
) -> BackupResult:
    """Zip the data folder as `fops-backup-<date>.zip`, newest wins per day.

    The store is checkpointed first: in WAL mode the database file alone can be
    missing committed data that is still in its `-wal` sidecar, which would make
    the backup quietly incomplete.
    """
    if store is not None:
        store.checkpoint()
    destination_dir.mkdir(parents=True, exist_ok=True)
    archive_path = destination_dir / backup_name(today)
    backups_folder = destination_dir.resolve()
    written = 0
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(data_dir.rglob("*")):
            if not path.is_file() or not _should_include(path):
                continue
            if path.resolve().is_relative_to(backups_folder):
                continue
            archive.write(path, path.relative_to(data_dir).as_posix())
            written += 1
    return BackupResult(archive_path, written)


def restore(archive_path: Path, data_dir: Path, force: bool = False) -> int:
    """Unpack a backup into an empty data folder (or any folder, with force)."""
    if data_dir.exists() and any(data_dir.iterdir()) and not force:
        raise RestoreRefused(
            f"{data_dir} is not empty. Move it aside first, or pass --force if you"
            " really mean to overwrite what is there."
        )
    data_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path) as archive:
        names = [name for name in archive.namelist() if not name.endswith("/")]
        for name in names:
            # Never let an archive entry escape the data folder.
            target = (data_dir / name).resolve()
            if not target.is_relative_to(data_dir.resolve()):
                raise RestoreRefused(f"{name} would land outside {data_dir}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(name))
    return len(names)
