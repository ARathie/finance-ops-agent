"""Whole runs on fakes: the scenarios named in docs/roadmap.md PR 5."""

from datetime import date

import pytest

from finance_ops_agent.domain.money import Hours, Money
from finance_ops_agent.domain.periods import BillingPeriod
from finance_ops_agent.domain.reading import ReadingHints, TimesheetReading
from finance_ops_agent.domain.statuses import ItemStatus
from tests.scenarios.conftest import DANA, PRIYA, ScenarioEnv, reading

AUG_START, AUG_END = date(2026, 8, 1), date(2026, 8, 31)


def open_codes(env: ScenarioEnv) -> set[str]:
    return {review.code for review in env.store.open_reviews()}


class TestHappyPath:
    def test_one_clean_timesheet_becomes_ready(self, env: ScenarioEnv) -> None:
        env.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END))
        env.run()

        item = env.store.find_item("Priya Shah", "Acme Corp", BillingPeriod(AUG_START, AUG_END))
        assert item is not None
        assert item.status is ItemStatus.READY
        assert item.approved_hours == Hours(15_600)
        assert item.invoice_amount == Money(2_184_000)  # the worked example
        assert item.amount_owed == Money(1_560_000)
        assert open_codes(env) == set()
        kinds = {record.kind for record in env.store.outgoing_records()}
        assert kinds == {"details_email", "preview_email", "payment_email"}

    def test_the_expected_item_is_reused_not_duplicated(self, env: ScenarioEnv) -> None:
        env.run()  # first run: period ended, no timesheet yet
        waiting = env.store.find_item("Priya Shah", "Acme Corp", BillingPeriod(AUG_START, AUG_END))
        assert waiting is not None
        assert waiting.status is ItemStatus.WAITING_FOR_TIMESHEET

        env.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END))
        env.run()
        assert len(env.store.list_items()) == 1
        assert env.store.get_item(waiting.id).status is ItemStatus.READY


class TestDuplicates:
    def test_same_file_twice(self, env: ScenarioEnv) -> None:
        env.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END))
        env.add_email(PRIYA, subject="Resending just in case")
        report = env.run()

        assert report.duplicates_filed == 1
        assert len(env.store.list_items()) == 1
        assert open_codes(env) == set()
        details = [r for r in env.store.outgoing_records() if r.kind == "details_email"]
        assert len(details) == 1

    def test_same_file_forwarded_by_someone_else(self, env: ScenarioEnv) -> None:
        env.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END))
        env.add_email(DANA, subject="FW: Priya's timesheet")
        report = env.run()

        assert report.duplicates_filed == 1
        assert len(env.store.list_items()) == 1

    def test_same_email_arriving_again_is_skipped(self, env: ScenarioEnv) -> None:
        env.add_email(
            PRIYA, scripted_reading=reading(AUG_START, AUG_END), message_id="<same@example>"
        )
        env.add_email(PRIYA, message_id="<same@example>", attachment=("other.pdf", b"X"))
        report = env.run()
        assert report.messages_stored == 1


class TestForwardedTimesheets:
    """Decision 25: for the first cycle, old timesheets are forwarded by hand.

    The forwarder's address says nothing about whose timesheet it is, so the
    consultant has to come off the document. That is the same fallback a real
    consultant's mail would use if they wrote from a new address.
    """

    FORWARDER = "meghan.rathie@gmail.com"

    def test_a_forward_from_an_unnamed_address_is_still_set_aside(self, env: ScenarioEnv) -> None:
        env.add_email(
            self.FORWARDER,
            subject="FW: Priya's August timesheet",
            scripted_reading=reading(AUG_START, AUG_END),
        )
        env.run()

        assert open_codes(env) == {"UNKNOWN_SENDER"}
        item = env.the_item()
        assert item.status is ItemStatus.WAITING_FOR_TIMESHEET

    def test_a_named_forwarder_gets_the_timesheet_read(self, env: ScenarioEnv) -> None:
        env.forwarders = (self.FORWARDER,)
        env.add_email(
            self.FORWARDER,
            subject="FW: Priya's August timesheet",
            scripted_reading=reading(AUG_START, AUG_END),
        )
        env.run()

        item = env.store.find_item("Priya Shah", "Acme Corp", BillingPeriod(AUG_START, AUG_END))
        assert item is not None
        assert item.status is ItemStatus.READY
        # The money is the worked example, reached from a forwarded email.
        assert item.invoice_amount == Money(2_184_000)
        assert item.amount_owed == Money(1_560_000)
        assert open_codes(env) == set()

    def test_the_consultant_comes_off_the_document_not_the_forwarder(
        self, env: ScenarioEnv
    ) -> None:
        env.forwarders = (self.FORWARDER,)
        env.add_email(
            self.FORWARDER,
            subject="FW: timesheet",
            scripted_reading=reading(AUG_START, AUG_END, consultant="Dana Cruz"),
        )
        env.run()

        # Priya's own expected item is untouched: this was not her timesheet.
        priya = env.store.find_item("Priya Shah", "Acme Corp", BillingPeriod(AUG_START, AUG_END))
        assert priya is not None and priya.status is ItemStatus.WAITING_FOR_TIMESHEET
        # Read as Dana's, whose engagement does not cover August, so it asks
        # rather than filing it under the only other consultant it knows.
        assert open_codes(env) == {"ENGAGEMENT_UNCLEAR"}

    def test_a_forward_naming_nobody_asks_kevin_rather_than_guessing(
        self, env: ScenarioEnv
    ) -> None:
        env.forwarders = (self.FORWARDER,)
        env.add_email(
            self.FORWARDER,
            subject="FW: timesheet",
            scripted_reading=reading(AUG_START, AUG_END, consultant="Someone Not On The List"),
        )
        env.run()

        assert "CONSULTANT_UNKNOWN" in open_codes(env)


class TestTheWholeWayThroughToMoney:
    """The path that actually bills: run -> complete_if_covered -> amounts.

    The checks were right and this path called them without the billing
    period, so a document holding days from two months billed all of them.
    A real May timesheet went out at 192 hours where 168 were worked.
    """

    def test_only_the_months_own_days_reach_the_invoice(self, env: ScenarioEnv) -> None:
        spilling = [
            (date(2026, 7, 30), 800),  # July: another invoice's day
            (date(2026, 7, 31), 800),  # July
            *[(date(2026, 8, day), 800) for day in (3, 4, 5, 6, 7)],
        ]
        env.add_email(
            PRIYA,
            scripted_reading=reading(
                date(2026, 7, 30), AUG_END, total_hundredths=None, dailies=spilling
            ),
        )
        env.run()

        item = env.store.find_item("Priya Shah", "Acme Corp", BillingPeriod(AUG_START, AUG_END))
        assert item is not None
        assert item.status is ItemStatus.READY
        assert item.approved_hours == Hours(4_000), "the two July days were billed"
        assert item.invoice_amount == Money(560_000)  # 40 h x 140.00

    def test_a_timesheet_naming_its_month_does_not_wait_for_the_last_days(
        self, env: ScenarioEnv
    ) -> None:
        """A weekly timesheet's last row is dated the week's first day, so its
        span stops short of the month end. Waiting for those days waits for
        ever; the stated month is what settles coverage (decision 26)."""
        env.add_email(
            PRIYA,
            scripted_reading=reading(
                date(2026, 7, 26), date(2026, 8, 22), month=AUG_START, total_hundredths=16_800
            ),
        )
        env.run()

        item = env.the_item()
        assert item.status is ItemStatus.READY, "it waited for days it will never be sent"
        assert item.approved_hours == Hours(16_800)


class TestCorrections:
    def test_corrected_before_sent(self, env: ScenarioEnv) -> None:
        env.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END))
        env.run()
        env.add_email(
            PRIYA,
            subject="Corrected timesheet",
            attachment=("timesheet-v2.pdf", b"PDFDATA2"),
            scripted_reading=reading(AUG_START, AUG_END, total_hundredths=15_000),
        )
        env.run()

        item = env.the_item()
        assert item.status is ItemStatus.NEEDS_REVIEW
        assert "CORRECTION" in open_codes(env)
        records = env.store.timesheets_for_item(item.id)
        assert [record.is_correction for record in records] == [False, True]

    def test_corrected_after_sent(self, env: ScenarioEnv) -> None:
        env.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END))
        env.run()
        item = env.the_item()
        env.store.change_status(item.id, ItemStatus.INVOICE_SENT, {})

        env.add_email(
            PRIYA,
            subject="Corrected timesheet",
            attachment=("timesheet-v2.pdf", b"PDFDATA2"),
            scripted_reading=reading(AUG_START, AUG_END, total_hundredths=15_000),
        )
        env.run()
        assert env.store.get_item(item.id).status is ItemStatus.NEEDS_REVIEW
        assert "CORRECTION" in open_codes(env)


class TestReviews:
    def test_unknown_sender(self, env: ScenarioEnv) -> None:
        name = env.add_email("stranger@nowhere.example")
        report = env.run()

        assert report.unknown_senders == 1
        assert "UNKNOWN_SENDER" in open_codes(env)
        assert env.mailbox.folders[f"<{name}@example>"] == "Needs Review"
        assert len(env.store.list_items()) == 1  # only the expected August item
        assert env.the_item().status is ItemStatus.WAITING_FOR_TIMESHEET

    def test_no_approval(self, env: ScenarioEnv) -> None:
        env.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END, approved=False))
        env.run()
        assert "NO_APPROVAL" in open_codes(env)
        assert env.the_item().status is ItemStatus.NEEDS_REVIEW

    def test_daily_hours_not_adding_up(self, env: ScenarioEnv) -> None:
        dailies = [(date(2026, 8, 3), 800), (date(2026, 8, 4), 800)]
        env.add_email(
            PRIYA,
            scripted_reading=reading(AUG_START, AUG_END, total_hundredths=15_600, dailies=dailies),
        )
        env.run()
        assert "HOURS_DONT_ADD_UP" in open_codes(env)

    def test_dates_not_matching_the_schedule(self, env: ScenarioEnv) -> None:
        env.add_email(
            PRIYA,
            scripted_reading=reading(date(2026, 8, 15), date(2026, 9, 14), total_hundredths=8_000),
        )
        env.run()
        assert "PERIOD_MISMATCH" in open_codes(env)

    def test_unreadable_attachment(self, env: ScenarioEnv) -> None:
        env.add_email(PRIYA, attachment=("mystery.bin", b"???"))  # nothing scripted
        env.run()
        assert "CANT_READ_ATTACHMENT" in open_codes(env)

    def test_no_attachment(self, env: ScenarioEnv) -> None:
        env.add_email(PRIYA, attachment=None)
        env.run()
        assert "NO_ATTACHMENT" in open_codes(env)

    def test_one_review_email_lists_everything(self, env: ScenarioEnv) -> None:
        env.add_email(
            PRIYA,
            scripted_reading=reading(AUG_START, AUG_END, approved=False, total_hundredths=0),
        )
        env.run()
        assert {"NO_APPROVAL", "HOURS_UNUSUAL"} <= open_codes(env)
        review_emails = [
            record for record in env.store.outgoing_records() if record.kind == "review_email"
        ]
        assert len(review_emails) == 1
        body = str(review_emails[0].payload["body"])
        assert "approved" in body.lower() and "unusual" in body.lower()


class TestWeeklyIntoMonthly:
    WEEKS = [
        (date(2026, 8, 1), date(2026, 8, 7)),
        (date(2026, 8, 8), date(2026, 8, 14)),
        (date(2026, 8, 15), date(2026, 8, 21)),
        (date(2026, 8, 22), date(2026, 8, 28)),
        (date(2026, 8, 29), date(2026, 8, 31)),
    ]

    def test_waits_until_the_whole_month_is_covered(self, env: ScenarioEnv) -> None:
        for index, (start, end) in enumerate(self.WEEKS[:-1]):
            env.add_email(
                PRIYA,
                subject=f"Week {index + 1}",
                attachment=(f"week-{index + 1}.pdf", f"WEEK{index + 1}".encode()),
                scripted_reading=reading(start, end, total_hundredths=3_500),
            )
        env.run()
        item = env.the_item()
        assert item.status is ItemStatus.RECEIVED
        assert item.approved_hours is None

        start, end = self.WEEKS[-1]
        env.add_email(
            PRIYA,
            subject="Last days of August",
            attachment=("week-5.pdf", b"WEEK5"),
            scripted_reading=reading(start, end, total_hundredths=800),
        )
        env.run()
        item = env.the_item()
        assert item.status is ItemStatus.READY
        assert item.approved_hours == Hours(4 * 3_500 + 800)
        assert item.invoice_amount == Money((4 * 3_500 + 800) * 140)


class TestRateChangeMidPeriod:
    def test_is_a_review_not_a_split_invoice(self, env: ScenarioEnv) -> None:
        from tests.scenarios.conftest import engagement_row

        env.workbook.engagements.append(
            engagement_row(
                4,
                **{"Bill rate": "150.00", "Pay rate": "105.00", "Rates from": "2026-08-15"},
            )
        )
        env.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END))
        env.run()

        assert "LIST_ROW_PROBLEM" in open_codes(env)
        messages = [review.message for review in env.store.open_reviews()]
        assert any("middle of this billing period" in message for message in messages)


class TestNeverTwice:
    def test_running_the_same_mailbox_twice_changes_nothing(self, env: ScenarioEnv) -> None:
        env.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END))
        env.add_email("stranger@nowhere.example")
        env.run()

        def state() -> tuple[object, ...]:
            return (
                env.store.list_items(),
                env.store.open_reviews(),
                env.store.outgoing_records(),
                len(env.store.audit_entries()),
            )

        first = state()
        report = env.run()
        assert state() == first
        assert report.messages_stored == 0
        assert report.timesheets_processed == 0
        assert report.reviews_opened == 0

    def test_stopping_half_way_and_restarting(self, env: ScenarioEnv) -> None:
        env.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END))
        env.add_email(
            DANA,
            subject="September week 1",
            attachment=("dana-week.pdf", b"DANA1"),
            scripted_reading=reading(
                date(2026, 9, 1),
                date(2026, 9, 7),
                total_hundredths=3_500,
                consultant="Dana Cruz",
            ),
        )

        # The run dies in the middle of the second timesheet.
        from dataclasses import replace as dc_replace

        from finance_ops_agent.adapters.fakes.reader import FakeReader
        from finance_ops_agent.application.run import run_once

        class CrashingReader(FakeReader):
            def read_timesheet(
                self,
                content: bytes,
                filename: str,
                mime_type: str,
                hints: ReadingHints | None = None,
            ) -> TimesheetReading:
                if filename == "dana-week.pdf":
                    raise RuntimeError("the machine went down here")
                return super().read_timesheet(content, filename, mime_type, hints)

        crashing_deps = dc_replace(env.deps(), reader=CrashingReader(env.readings.copy()))
        with pytest.raises(RuntimeError):
            run_once(crashing_deps)

        env.run()  # restart, healthy

        items = {(item.consultant, item.status) for item in env.store.list_items()}
        assert ("Priya Shah", ItemStatus.READY) in items
        assert ("Dana Cruz", ItemStatus.RECEIVED) in items
        details = [
            record for record in env.store.outgoing_records() if record.kind == "details_email"
        ]
        assert len(details) == 2  # one per timesheet, never twice
        assert len(env.store.list_items()) == 2
