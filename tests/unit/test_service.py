"""`fops serve` on a fake clock: cadence, the nightly backup, the heartbeat,
no overlap, and a prompt stop on SIGTERM (roadmap PR 14)."""

import os
import signal
import threading
import time as wall
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from finance_ops_agent.application.service import (
    Schedule,
    ServiceReport,
    ServiceSteps,
    backup_due,
    serve,
)
from finance_ops_agent.cli.serve import install_stop_signals

ZONE = ZoneInfo("America/New_York")
START = datetime(2026, 10, 29, 9, 0, tzinfo=ZONE)
SCHEDULE = Schedule(run_every=timedelta(minutes=15), backup_at=time(2, 30))


class FakeService:
    """Time moves only when the loop waits or a step takes time."""

    def __init__(
        self,
        until: datetime,
        run_takes: timedelta = timedelta(seconds=5),
        run_ok: Callable[[int], bool] = lambda n: True,
        backup_ok: Callable[[int], bool] = lambda n: True,
        last_backup: date | None = None,
    ) -> None:
        self.clock = START
        self.until = until
        self.run_takes = run_takes
        self.run_ok = run_ok
        self.backup_ok = backup_ok
        self.remembered = last_backup
        self.runs: list[datetime] = []
        self.backups: list[datetime] = []
        self.pings: list[datetime] = []
        self.busy = False

    def now(self) -> datetime:
        return self.clock

    def wait(self, seconds: float) -> bool:
        assert seconds >= 0
        self.clock += timedelta(seconds=seconds)
        return self.stop_requested()

    def stop_requested(self) -> bool:
        return self.clock >= self.until

    def run(self) -> bool:
        assert not self.busy, "a run started while another was going"
        self.busy = True
        self.runs.append(self.clock)
        self.clock += self.run_takes
        self.busy = False
        return self.run_ok(len(self.runs))

    def backup(self, day: date) -> bool:
        assert not self.busy, "a backup started during a run"
        self.backups.append(self.clock)
        ok = self.backup_ok(len(self.backups))
        if ok:
            self.remembered = day
        return ok

    def ping(self) -> None:
        self.pings.append(self.clock)

    def serve(self) -> ServiceReport:
        return serve(
            ServiceSteps(
                now=self.now,
                wait=self.wait,
                stop_requested=self.stop_requested,
                run=self.run,
                backup=self.backup,
                ping=self.ping,
                last_backup=lambda: self.remembered,
            ),
            SCHEDULE,
        )


class TestCadence:
    def test_a_run_straight_away_then_every_fifteen_minutes(self) -> None:
        fake = FakeService(until=START + timedelta(hours=1, minutes=1))
        fake.serve()
        assert [run - START for run in fake.runs] == [
            timedelta(minutes=minutes) for minutes in (0, 15, 30, 45, 60)
        ]

    def test_a_whole_day_is_96_runs_and_one_backup(self) -> None:
        fake = FakeService(until=START + timedelta(days=1), last_backup=START.date())
        report = fake.serve()
        assert report.runs == 96
        assert len(fake.backups) == 1
        assert fake.backups[0].date() == date(2026, 10, 30)
        assert fake.backups[0].time() >= time(2, 30)
        assert fake.backups[0] - datetime(2026, 10, 30, 2, 30, tzinfo=ZONE) < timedelta(minutes=1)

    def test_a_slow_run_is_followed_by_one_more_not_a_backlog(self) -> None:
        fake = FakeService(
            until=START + timedelta(minutes=50),
            run_takes=timedelta(minutes=40),
            last_backup=START.date(),
        )
        fake.serve()
        assert [run - START for run in fake.runs] == [timedelta(0), timedelta(minutes=40)]


class TestBackups:
    def test_a_service_started_after_the_hour_backs_up_that_day(self) -> None:
        fake = FakeService(until=START + timedelta(minutes=5))
        fake.serve()
        assert len(fake.backups) == 1

    def test_one_already_done_today_is_not_repeated_after_a_restart(self) -> None:
        fake = FakeService(until=START + timedelta(hours=3), last_backup=START.date())
        fake.serve()
        assert fake.backups == []

    def test_a_failed_backup_is_tried_again_an_hour_later_not_every_minute(self) -> None:
        fake = FakeService(until=START + timedelta(hours=2, minutes=30), backup_ok=lambda n: n > 2)
        fake.serve()
        assert [b - START for b in fake.backups] == [
            timedelta(seconds=5),
            timedelta(hours=1, seconds=5),
            timedelta(hours=2, seconds=5),
        ]

    def test_backup_due(self) -> None:
        at = time(2, 30)
        night = datetime(2026, 10, 30, 2, 30, tzinfo=ZONE)
        assert backup_due(night, at, date(2026, 10, 29))
        assert not backup_due(night - timedelta(minutes=1), at, date(2026, 10, 29))
        assert not backup_due(night, at, date(2026, 10, 30))
        assert backup_due(night, at, None)


class TestHeartbeat:
    def test_pinged_after_every_good_run_and_backup(self) -> None:
        fake = FakeService(until=START + timedelta(minutes=31))
        report = fake.serve()
        assert report.runs == 3
        assert len(fake.pings) == 3 + 1

    def test_never_after_a_run_that_failed(self) -> None:
        fake = FakeService(
            until=START + timedelta(minutes=46),
            run_ok=lambda n: n % 2 == 0,
            last_backup=START.date(),
        )
        report = fake.serve()
        assert report.runs == 4
        assert fake.pings == [fake.runs[1] + fake.run_takes, fake.runs[3] + fake.run_takes]

    def test_never_after_a_backup_that_failed(self) -> None:
        fake = FakeService(until=START + timedelta(minutes=1), backup_ok=lambda n: False)
        fake.serve()
        assert len(fake.backups) == 1
        assert len(fake.pings) == 1  # the run's only


class TestStopping:
    def test_a_stop_during_a_run_ends_the_loop_without_another_step(self) -> None:
        fake = FakeService(until=START + timedelta(seconds=3))
        report = fake.serve()
        assert report.runs == 1
        assert fake.backups == []

    def test_sigterm_while_waiting_stops_the_real_loop_at_once(self) -> None:
        """The real thing: a signal, a real Event, real waiting."""
        stop = threading.Event()
        previous = signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT)
        install_stop_signals(stop)
        runs: list[float] = []

        def run() -> bool:
            runs.append(wall.monotonic())
            return True

        try:
            threading.Timer(0.3, os.kill, (os.getpid(), signal.SIGTERM)).start()
            started = wall.monotonic()
            serve(
                ServiceSteps(
                    now=lambda: datetime.now(ZONE),
                    wait=stop.wait,
                    stop_requested=stop.is_set,
                    run=run,
                    backup=lambda day: True,
                    ping=lambda: None,
                    last_backup=lambda: datetime.now(ZONE).date(),
                ),
                SCHEDULE,
            )
            took = wall.monotonic() - started
        finally:
            signal.signal(signal.SIGTERM, previous[0])
            signal.signal(signal.SIGINT, previous[1])
        assert len(runs) == 1
        assert took < 30
        assert took < 5  # in practice, at once
