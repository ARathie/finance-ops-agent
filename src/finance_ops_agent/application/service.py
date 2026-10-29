"""`fops serve`: the agent as one long-running process (decision 22, roadmap PR 14).

A run every `run_every`, a backup once a day at `backup_at` in Icon's
timezone, and a heartbeat ping after each that succeeded. Nothing here touches
a socket, a file or a signal: the command line hands in what to do and how to
wait, so a test can drive a whole day on a fake clock in milliseconds.

Never overlapping is two promises. Inside this loop a run and a backup are
called one after the other and never at once; a run that takes longer than
the interval is followed by one more run straight away, not by a queue of
them. Across processes (`docker compose exec fops fops run` while the service
is up) the run lock in `data/run.lock` does it, which the command line takes
around both.

Stopping is the loop's own business only between steps. `wait` returns as
soon as a stop is asked for, and a run in progress stops itself at its next
safe point (`RunDeps.stop_requested`), never in the middle of a send.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from finance_ops_agent import logs

# The longest the loop sleeps at a time. A clock that jumps (a laptop waking,
# a virtual machine resumed) is noticed within this long.
LONGEST_WAIT = timedelta(minutes=1)
# A backup that failed (the bucket unreachable, say) is tried again this long
# after, not on every pass of the loop.
BACKUP_RETRY = timedelta(hours=1)


@dataclass
class ServiceSteps:
    # Icon's local time, timezone-aware: the backup hour is a local hour.
    now: Callable[[], datetime]
    # Sleep up to this many seconds; True if a stop was asked for meanwhile.
    wait: Callable[[float], bool]
    stop_requested: Callable[[], bool]
    # Each says whether it finished cleanly; only then is the heartbeat pinged.
    run: Callable[[], bool]
    backup: Callable[[date], bool]
    ping: Callable[[], None]
    # The day of the last backup that finished, remembered across restarts.
    last_backup: Callable[[], date | None]


@dataclass(frozen=True)
class Schedule:
    run_every: timedelta
    backup_at: time


@dataclass
class ServiceReport:
    runs: int = 0
    runs_ok: int = 0
    backups: int = 0
    pings: int = 0


def backup_due(current: datetime, backup_at: time, last: date | None) -> bool:
    """Once a day, at or after the backup hour.

    A service that was down at the hour backs up when it comes back that day,
    rather than waiting for tomorrow's; one that already backed up today does
    nothing more until tomorrow.
    """
    return current.time() >= backup_at and last != current.date()


def _next_backup(current: datetime, backup_at: time) -> datetime:
    today_at = current.replace(
        hour=backup_at.hour, minute=backup_at.minute, second=0, microsecond=0
    )
    return today_at if today_at > current else today_at + timedelta(days=1)


def serve(steps: ServiceSteps, schedule: Schedule) -> ServiceReport:
    """Run until a stop is asked for. The first run is straight away."""
    report = ServiceReport()
    logs.log(
        "service started",
        run_every_minutes=int(schedule.run_every.total_seconds() // 60),
        backup_at=schedule.backup_at.strftime("%H:%M"),
    )
    next_run = steps.now()
    backup_tried: datetime | None = None
    while not steps.stop_requested():
        current = steps.now()
        if current >= next_run:
            # Counted from when this run started, so a slow run is followed
            # by one more at once and never by a backlog.
            next_run = current + schedule.run_every
            report.runs += 1
            if steps.run():
                report.runs_ok += 1
                steps.ping()
                report.pings += 1
            if steps.stop_requested():
                break
        current = steps.now()
        retrying_too_soon = backup_tried is not None and current - backup_tried < BACKUP_RETRY
        if not retrying_too_soon and backup_due(current, schedule.backup_at, steps.last_backup()):
            backup_tried = current
            report.backups += 1
            if steps.backup(current.date()):
                steps.ping()
                report.pings += 1
            if steps.stop_requested():
                break
        current = steps.now()
        wake = min(next_run, _next_backup(current, schedule.backup_at), current + LONGEST_WAIT)
        if steps.wait(max((wake - current).total_seconds(), 0.0)):
            break
    logs.log("service stopped", runs=report.runs, backups=report.backups)
    return report
