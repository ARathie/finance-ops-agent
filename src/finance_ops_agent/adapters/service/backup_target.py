"""The backup bucket, reached through rclone.

`FOPS_BACKUP_TARGET` is an rclone remote path such as `b2:icon-fops-backups`
(configured once on the server with `rclone config`), or for a test a plain
folder. rclone rather than a library per provider: B2, S3, Spaces and the rest
are one setting apart, and the operator can list and fetch backups with the
same tool by hand.
"""

import subprocess
from pathlib import Path

TIMEOUT_SECONDS = 600


class BackupTargetFailed(Exception):
    """rclone is missing, or the bucket could not be reached or written."""


class RcloneTarget:
    def __init__(self, remote: str, rclone: str = "rclone") -> None:
        self.remote = remote.rstrip("/")
        self._rclone = rclone

    def _call(self, *args: str) -> str:
        try:
            result = subprocess.run(  # noqa: S603 - fixed program, no shell
                [self._rclone, *args],
                capture_output=True,
                text=True,
                timeout=TIMEOUT_SECONDS,
                check=False,
            )
        except FileNotFoundError as error:
            raise BackupTargetFailed(f"{self._rclone} is not installed") from error
        except subprocess.TimeoutExpired as error:
            raise BackupTargetFailed(f"rclone {args[0]} took too long") from error
        if result.returncode != 0:
            last = (result.stderr.strip().splitlines() or ["no message"])[-1]
            raise BackupTargetFailed(f"rclone {args[0]} failed: {last}")
        return result.stdout

    def _path(self, name: str) -> str:
        return f"{self.remote}/{name}"

    def upload(self, archive: Path) -> None:
        self._call("copyto", str(archive), self._path(archive.name))

    def names(self) -> list[str]:
        """The files at the top of the target (no folders)."""
        listing = self._call("lsf", "--files-only", "--max-depth", "1", self.remote)
        return [line.strip() for line in listing.splitlines() if line.strip()]

    def delete(self, name: str) -> None:
        self._call("deletefile", self._path(name))
