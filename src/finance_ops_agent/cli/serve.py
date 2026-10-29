"""`fops serve`: the container's command (docs/running-it.md stage 2).

The loop itself is `application/service.py`; this wires it to the real clock,
the real run, the backup, the heartbeat and the bucket, and to SIGTERM, which
is how `docker compose stop` (and systemd) ask a process to finish.
"""

import argparse
import signal
import threading
from datetime import date, datetime, timedelta
from types import FrameType
from zoneinfo import ZoneInfo

from finance_ops_agent import logs

LAST_BACKUP_KEY = "last_backup"


def install_stop_signals(stop: threading.Event) -> None:
    """SIGTERM and Ctrl-C both ask the loop to finish; neither kills it mid-step."""

    def asked(signum: int, frame: FrameType | None) -> None:
        if not stop.is_set():
            logs.log("stop asked for", signal=signal.Signals(signum).name)
        stop.set()

    signal.signal(signal.SIGTERM, asked)
    signal.signal(signal.SIGINT, asked)


def command_serve(args: argparse.Namespace) -> int:
    from finance_ops_agent.adapters.lockfile import AlreadyRunning, RunLock
    from finance_ops_agent.adapters.service.backup_target import BackupTargetFailed, RcloneTarget
    from finance_ops_agent.adapters.service.heartbeat import Heartbeat
    from finance_ops_agent.application.backup import (
        back_up,
        backups_to_delete,
        local_backups_to_delete,
    )
    from finance_ops_agent.application.context import RunStopped
    from finance_ops_agent.application.run import run_once
    from finance_ops_agent.application.service import Schedule, ServiceSteps, serve
    from finance_ops_agent.cli.main import _open_store, _real_deps, _warn_about_ageing_connections
    from finance_ops_agent.config import (
        Config,
        MailSettings,
        MissingSettingError,
        ServiceSettings,
    )

    try:
        config = Config.from_env()
        service = ServiceSettings.from_env()
        MailSettings.from_env()  # checked before the loop starts, not on every run
    except MissingSettingError as error:
        print(f"Not configured: {error}. Run `fops doctor` for the whole list.")
        return 2

    logs.configure(config.data_dir / "fops.log")
    stop = threading.Event()
    install_stop_signals(stop)
    zone = ZoneInfo(config.timezone)
    lock_path = config.data_dir / "run.lock"
    # Opened once: the database is migrated here, when the container starts.
    store = _open_store(config.data_dir)
    heartbeat = Heartbeat(service.heartbeat_url)
    target = RcloneTarget(service.backup_target) if service.backup_target else None
    backups_dir = config.data_dir / "backups"
    if not service.heartbeat_url:
        logs.log("no heartbeat set: nobody will be told if the agent stops")
    if target is None:
        logs.log("no backup target set: backups stay on this machine only")

    def one_run() -> bool:
        try:
            deps = _real_deps(store=store)
            deps.stop_requested = stop.is_set
            with RunLock(lock_path):
                run_once(deps)
        except AlreadyRunning:
            logs.log("run skipped: another run holds the lock")
            return False
        except RunStopped:
            logs.log("run stopped early: shutting down")
            return False
        except Exception:
            # One bad run must not end the service; the heartbeat stays quiet,
            # so a run that keeps failing is noticed all the same.
            logs.logger().exception("run failed")
            return False
        _warn_about_ageing_connections(config)
        return True

    def one_backup(day: date) -> bool:
        try:
            with RunLock(lock_path):
                result = back_up(config.data_dir, backups_dir, day, store=store)
            for name in local_backups_to_delete(path.name for path in backups_dir.iterdir()):
                (backups_dir / name).unlink()
            if target is not None:
                target.upload(result.path)
                for name in backups_to_delete(target.names(), day):
                    target.delete(name)
        except AlreadyRunning:
            logs.log("backup waiting: another run holds the lock")
            return False
        except (BackupTargetFailed, OSError) as error:
            logs.log("backup failed", error=str(error)[:300])
            return False
        store.set_state(LAST_BACKUP_KEY, day.isoformat())
        logs.log("backup done", files=result.files, uploaded=target is not None)
        return True

    def last_backup() -> date | None:
        text = store.get_state(LAST_BACKUP_KEY)
        return date.fromisoformat(text) if text else None

    def now() -> datetime:
        return datetime.now(zone)

    print(
        f"Serving in {config.mode} mode: a run every {service.run_every_minutes} minute(s),"
        f" a backup at {service.backup_at.strftime('%H:%M')} ({config.timezone})."
        " SIGTERM or Ctrl-C stops it."
    )
    serve(
        ServiceSteps(
            now=now,
            wait=stop.wait,
            stop_requested=stop.is_set,
            run=one_run,
            backup=one_backup,
            ping=heartbeat.ping,
            last_backup=last_backup,
        ),
        Schedule(
            run_every=timedelta(minutes=service.run_every_minutes),
            backup_at=service.backup_at,
        ),
    )
    return 0
