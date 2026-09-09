"""Two overlapping runs cannot happen."""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from finance_ops_agent.adapters.lockfile import AlreadyRunning, RunLock


def test_the_lock_can_be_taken_and_released(tmp_path: Path) -> None:
    lock = RunLock(tmp_path / "run.lock")
    with lock:
        assert lock.path.exists()
    # Released, so it can be taken again.
    with RunLock(tmp_path / "run.lock"):
        pass


def test_a_second_run_in_another_process_is_refused(tmp_path: Path) -> None:
    """The real case: the scheduler fires while a slow run is still going."""
    lock_path = tmp_path / "run.lock"
    holder = subprocess.Popen(  # noqa: S603
        [
            sys.executable,
            "-c",
            textwrap.dedent(
                f"""
                import sys, time, pathlib
                sys.path.insert(0, {str(Path("src").resolve())!r})
                from finance_ops_agent.adapters.lockfile import RunLock
                with RunLock(pathlib.Path({str(lock_path)!r})):
                    print("holding", flush=True)
                    time.sleep(30)
                """
            ),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "holding"

        with pytest.raises(AlreadyRunning) as error:
            RunLock(lock_path).acquire()
        assert "another run is already going" in str(error.value)
        assert str(holder.pid) in str(error.value)  # says who holds it
    finally:
        holder.kill()
        holder.wait()


def test_the_lock_is_released_when_the_holder_dies(tmp_path: Path) -> None:
    """A killed run must not lock the agent out forever, which a pid file would."""
    lock_path = tmp_path / "run.lock"
    holder = subprocess.Popen(  # noqa: S603
        [
            sys.executable,
            "-c",
            textwrap.dedent(
                f"""
                import sys, time, pathlib
                sys.path.insert(0, {str(Path("src").resolve())!r})
                from finance_ops_agent.adapters.lockfile import RunLock
                lock = RunLock(pathlib.Path({str(lock_path)!r}))
                lock.acquire()
                print("holding", flush=True)
                time.sleep(30)
                """
            ),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert holder.stdout is not None
    assert holder.stdout.readline().strip() == "holding"
    holder.kill()
    holder.wait()

    with RunLock(lock_path):  # the operating system dropped the dead process's lock
        pass


def test_the_lock_file_is_owner_only(tmp_path: Path) -> None:
    lock = RunLock(tmp_path / "run.lock")
    with lock:
        assert lock.path.stat().st_mode & 0o077 == 0
        assert lock.path.read_text().strip() == str(os.getpid())
