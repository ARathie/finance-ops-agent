"""One run at a time.

Two runs at once could send the same email twice or invoice the same period
twice, so the scheduler's every-15-minutes must never overlap with a run that
is still going. A POSIX advisory lock on a file under data/ does this: the lock
belongs to the process holding it, so it is released even if the run is killed,
which a "is there a pid file?" check cannot promise.
"""

import contextlib
import fcntl
import os
from pathlib import Path
from types import TracebackType


class AlreadyRunning(Exception):
    """Another run holds the lock; this one should simply stop."""


class RunLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._descriptor: int | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            os.close(descriptor)
            held_by = ""
            with contextlib.suppress(OSError):
                held_by = self.path.read_text().strip()
            who = f" (held by process {held_by})" if held_by else ""
            raise AlreadyRunning(
                f"another run is already going{who}. This one is stopping;"
                " the next scheduled run will pick things up."
            ) from error
        os.truncate(descriptor, 0)
        os.write(descriptor, f"{os.getpid()}\n".encode())
        os.fsync(descriptor)
        self._descriptor = descriptor

    def release(self) -> None:
        if self._descriptor is None:
            return
        # Closing drops the advisory lock; the file is left in place so its
        # contents show who ran last.
        fcntl.flock(self._descriptor, fcntl.LOCK_UN)
        os.close(self._descriptor)
        self._descriptor = None

    def __enter__(self) -> "RunLock":
        self.acquire()
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()
