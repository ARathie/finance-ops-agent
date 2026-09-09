"""`fops backup` and `fops restore`: the data folder in one zip.

Everything the agent cannot rebuild lives under data/ - the database, the
stored attachments and PDFs, the QuickBooks tokens, the mailbox cursor. A
backup is worth nothing until a restore has been tried, so restore is a real
command with a real test, not a note in a runbook.

The lock file is deliberately left out: it belongs to a running process, not to
the data.
"""

import zipfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from finance_ops_agent.ports.store import Store

SKIPPED_NAMES = frozenset({"run.lock"})
# The sidecar files belong to a live connection. They are safe to leave out
# only because back_up checkpoints the database first.
SKIPPED_SUFFIXES = (".db-wal", ".db-shm")


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
    archive_path = destination_dir / f"fops-backup-{today.isoformat()}.zip"
    written = 0
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(data_dir.rglob("*")):
            if not path.is_file() or not _should_include(path):
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
