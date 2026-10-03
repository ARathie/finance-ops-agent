"""The agent's own copy of the engagements, and what Kevin hears when it cannot
do its job (decision 55).

The copy is taken from QuickBooks on the day's first run and again whenever
something does not match it. Rates are never taken from it alone: they are
asked for again, for the engagement in hand, when its timesheet is read. Every
way this can go wrong ends in an email to Kevin saying what he can reply.
"""

from datetime import date

import pytest

from finance_ops_agent.application.engagement_copy import (
    COPY_KEY,
    workbook_from_json,
    workbook_to_json,
)
from finance_ops_agent.application.run import LAST_EXPECTED_CHECK_KEY
from finance_ops_agent.domain.engagements import RawWorkbook, parse_workbook
from finance_ops_agent.domain.reading import ReplyAnswer, ReplyAnswerKind, ReplyReading
from finance_ops_agent.domain.statuses import ItemStatus
from finance_ops_agent.ports.accounting import (
    AccountingEngagement,
    AccountingFailed,
    AccountingNeedsReconnect,
    AccountingParty,
    EngagementRates,
)
from finance_ops_agent.ports.inbox import NEEDS_REVIEW_FOLDER, PROCESSED_FOLDER
from tests.scenarios.conftest import PRIYA, ScenarioEnv, default_workbook, reading

AUG_START, AUG_END = date(2026, 8, 1), date(2026, 8, 31)
SAM = "sam@example.com"


def product(consultant: str, ref: str, vendor: str) -> AccountingEngagement:
    return AccountingEngagement(
        ref=ref,
        consultant=consultant,
        client="Acme Corp",
        bill_rate_cents=14_000,
        pay_rate_cents=10_000,
        payee=consultant,
        payee_ref=vendor,
        notes="Start: 2026-08-01",
    )


def add_to_quickbooks(env: ScenarioEnv, consultant: str, ref: str, vendor: str, email: str) -> None:
    """What Kevin does in QuickBooks for a new consultant."""
    env.accounting.live.append(product(consultant, ref, vendor))
    env.accounting.payees[vendor] = AccountingParty(
        ref=vendor, name=consultant, email=email, payment_terms_days=15
    )
    env.accounting.rates[(consultant, "Acme Corp")] = EngagementRates(
        ref=ref, bill_rate_cents=14_000, pay_rate_cents=10_000, payee=consultant, payee_ref=vendor
    )


@pytest.fixture
def qbo(env: ScenarioEnv) -> ScenarioEnv:
    env.engagements_from = "quickbooks"
    env.workbook = RawWorkbook(clients=[], consultants=[], vendors=[], engagements=[])
    env.accounting.customers["Acme Corp"] = AccountingParty(
        ref="58",
        name="Acme Corp",
        company="Acme Corporation",
        email="ap@acme.example",
        payment_terms_days=30,
        notes="Invoice code: AC",
    )
    add_to_quickbooks(env, "Priya Shah", "42", "7", PRIYA)
    return env


def kevin_says(env: ScenarioEnv, subject_starts: str, body: str, *answers: ReplyAnswer) -> None:
    subject = next(s for s in env.sent_subjects() if s.startswith(subject_starts))
    env.replies[body] = ReplyReading(answers=list(answers))
    env.reply_from_kevin(subject, body)


def answer(kind: ReplyAnswerKind, code: str = "UNKNOWN_SENDER", value: str = "") -> ReplyAnswer:
    return ReplyAnswer(review_code=code, kind=kind, value=value or None)


def open_codes(env: ScenarioEnv) -> list[str]:
    return [review.code for review in env.store.open_reviews()]


# --- the copy itself ---


class TestTheCopy:
    def test_it_survives_being_kept(self) -> None:
        workbook = parse_workbook(default_workbook())
        assert workbook_from_json(workbook_to_json(workbook)) == workbook

    def test_the_days_first_run_takes_it_and_later_runs_use_it(self, qbo: ScenarioEnv) -> None:
        qbo.run()
        assert qbo.accounting.calls["engagements"] == 1
        qbo.accounting.calls.clear()

        qbo.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END))
        qbo.run()

        # Priya is recognised from the copy; only her own engagement is asked about.
        assert qbo.accounting.calls["engagements"] == 0
        assert qbo.accounting.calls["engagement_rates"] == 1
        assert qbo.the_item().status is ItemStatus.READY

    def test_the_next_day_takes_a_new_one(self, qbo: ScenarioEnv) -> None:
        qbo.run()
        qbo.today = date(2026, 9, 9)
        qbo.run()
        assert qbo.accounting.calls["engagements"] == 2

    def test_a_copy_that_cannot_be_read_is_taken_again(self, qbo: ScenarioEnv) -> None:
        qbo.run()
        qbo.store.set_state(COPY_KEY, '{"not": "a workbook"}')
        qbo.today = date(2026, 9, 9)
        qbo.run()
        assert qbo.store.open_reviews() == []
        assert qbo.accounting.calls["engagements"] == 2


# --- a new consultant, after the morning's copy ---


class TestAnAddressNotInTheCopy:
    def test_a_fresh_copy_is_taken_and_the_timesheet_handled(self, qbo: ScenarioEnv) -> None:
        qbo.run()  # the morning's copy, before Sam was added
        add_to_quickbooks(qbo, "Sam Okafor", "44", "8", SAM)
        qbo.add_email(SAM, scripted_reading=reading(AUG_START, AUG_END, consultant="Sam Okafor"))

        qbo.run()

        assert qbo.accounting.calls["engagements"] == 2
        assert "UNKNOWN_SENDER" not in open_codes(qbo)
        sam = [item for item in qbo.store.list_items() if item.consultant == "Sam Okafor"]
        assert [item.status for item in sam] == [ItemStatus.READY]

    def test_quickbooks_is_asked_once_however_many_strangers_write(self, qbo: ScenarioEnv) -> None:
        qbo.run()
        qbo.add_email("one@example.com")
        qbo.add_email("two@example.com")
        qbo.run()
        assert qbo.accounting.calls["engagements"] == 2

    def test_still_unknown_is_set_aside_and_kevin_is_told_what_he_can_do(
        self, qbo: ScenarioEnv
    ) -> None:
        qbo.add_email(SAM)
        qbo.run()

        assert open_codes(qbo) == ["UNKNOWN_SENDER"]
        email = next(
            e for e in qbo.sender.sent_emails() if e.subject.startswith("Needs your review")
        )
        assert SAM in email.subject
        assert '"try again"' in email.body
        assert "this is from Priya Shah" in email.body
        assert '"ignore"' in email.body

    def test_without_an_attachment_it_waits_for_the_monday_summary(self, qbo: ScenarioEnv) -> None:
        """A newsletter is not worth an email of its own."""
        qbo.add_email("news@conference.example", attachment=None)
        qbo.run()

        assert open_codes(qbo) == ["UNKNOWN_SENDER"]
        assert not any(s.startswith("Needs your review") for s in qbo.sent_subjects())


class TestKevinAnswersAboutAnUnknownAddress:
    @pytest.fixture
    def stranger(self, qbo: ScenarioEnv) -> ScenarioEnv:
        qbo.add_email(SAM, scripted_reading=reading(AUG_START, AUG_END, consultant="Sam Okafor"))
        qbo.run()
        return qbo

    def test_try_again_after_adding_them_in_quickbooks(self, stranger: ScenarioEnv) -> None:
        add_to_quickbooks(stranger, "Sam Okafor", "44", "8", SAM)
        kevin_says(stranger, "Needs your review", "try again", answer(ReplyAnswerKind.TRY_AGAIN))

        stranger.run()

        assert open_codes(stranger) == []
        sam = [item for item in stranger.store.list_items() if item.consultant == "Sam Okafor"]
        assert [item.status for item in sam] == [ItemStatus.READY]
        [message_id] = stranger.store.message_ids_for_item(sam[0].id)
        assert stranger.mailbox.folders[message_id] == PROCESSED_FOLDER

    def test_fixing_quickbooks_alone_is_noticed_the_next_day(self, stranger: ScenarioEnv) -> None:
        add_to_quickbooks(stranger, "Sam Okafor", "44", "8", SAM)
        stranger.today = date(2026, 9, 9)
        stranger.run()

        assert "UNKNOWN_SENDER" not in open_codes(stranger)
        assert any(item.consultant == "Sam Okafor" for item in stranger.store.list_items())

    def test_try_again_while_still_unknown_says_so(self, stranger: ScenarioEnv) -> None:
        kevin_says(stranger, "Needs your review", "try again", answer(ReplyAnswerKind.TRY_AGAIN))
        stranger.run()

        stuck = [s for s in stranger.sent_subjects() if s.startswith("Still needs your review")]
        assert stuck == [f"Still needs your review: the email from {SAM}"]
        assert open_codes(stranger) == ["UNKNOWN_SENDER"]

    def test_this_is_from_someone_known(self, stranger: ScenarioEnv) -> None:
        """Kevin says whose it is: the agent takes his word for the sender and
        reads the rest of the timesheet as usual."""
        stranger.readings["timesheet.pdf"] = reading(AUG_START, AUG_END)
        kevin_says(
            stranger,
            "Needs your review",
            "this is from Priya Shah",
            answer(ReplyAnswerKind.CONSULTANT_NAME, value="Priya Shah"),
        )
        stranger.run()

        assert open_codes(stranger) == []
        assert stranger.the_item().consultant == "Priya Shah"
        assert stranger.the_item().status is ItemStatus.READY

    def test_naming_someone_who_is_not_there_says_so(self, stranger: ScenarioEnv) -> None:
        kevin_says(
            stranger,
            "Needs your review",
            "it's Zed",
            answer(ReplyAnswerKind.CONSULTANT_NAME, value="Zed Nobody"),
        )
        stranger.run()

        email = next(
            e for e in stranger.sender.sent_emails() if e.subject.startswith("Still needs")
        )
        assert '"Zed Nobody"' in email.body
        assert open_codes(stranger) == ["UNKNOWN_SENDER"]

    def test_ignore_closes_only_this_one(self, stranger: ScenarioEnv) -> None:
        stranger.store.open_review(None, "LIST_ROW_PROBLEM", "something else entirely")
        kevin_says(stranger, "Needs your review", "ignore", answer(ReplyAnswerKind.IGNORE))
        stranger.run()

        assert open_codes(stranger) == ["LIST_ROW_PROBLEM"]
        assert not any(item.consultant == "Sam Okafor" for item in stranger.store.list_items())
        # Adding Sam later does not bring an ignored email back: his August is
        # expected, as any engagement's is, but still waits for a timesheet.
        add_to_quickbooks(stranger, "Sam Okafor", "44", "8", SAM)
        stranger.today = date(2026, 9, 9)
        stranger.run()
        sam = [item for item in stranger.store.list_items() if item.consultant == "Sam Okafor"]
        assert [item.status for item in sam] == [ItemStatus.WAITING_FOR_TIMESHEET]

    def test_an_unclear_answer_is_asked_again(self, stranger: ScenarioEnv) -> None:
        kevin_says(stranger, "Needs your review", "hmm?", answer(ReplyAnswerKind.UNCLEAR))
        stranger.run()

        asked = [e for e in stranger.sender.sent_emails() if "couldn't tell" in e.body]
        assert len(asked) == 1
        assert '"try again"' in asked[0].body
        assert open_codes(stranger) == ["UNKNOWN_SENDER"]


# --- a timesheet naming someone the copy does not have ---


class TestATimesheetThatCannotBePlaced:
    @pytest.fixture
    def forwarded(self, qbo: ScenarioEnv) -> ScenarioEnv:
        qbo.forwarders = ("tester@example.com",)
        qbo.run()  # the morning's copy
        qbo.add_email(
            "tester@example.com",
            scripted_reading=reading(AUG_START, AUG_END, consultant="Sam Okafor"),
        )
        return qbo

    def test_a_fresh_copy_finds_someone_added_since_the_morning(
        self, forwarded: ScenarioEnv
    ) -> None:
        add_to_quickbooks(forwarded, "Sam Okafor", "44", "8", SAM)
        forwarded.run()

        sam = [i for i in forwarded.store.list_items() if i.consultant == "Sam Okafor"]
        assert [item.status for item in sam] == [ItemStatus.READY]

    def test_still_unplaced_is_set_aside_with_its_reading(self, forwarded: ScenarioEnv) -> None:
        forwarded.run()

        assert "CONSULTANT_UNKNOWN" in open_codes(forwarded)
        email = next(
            e for e in forwarded.sender.sent_emails() if e.subject.startswith("Needs your review")
        )
        assert '"try again"' in email.body
        assert email.attachments  # the timesheet, so Kevin can see who it is

    def test_try_again_reads_it_again_and_says_so_afresh(self, forwarded: ScenarioEnv) -> None:
        forwarded.run()
        add_to_quickbooks(forwarded, "Sam Okafor", "44", "8", SAM)
        kevin_says(
            forwarded,
            "Needs your review",
            "try again",
            answer(ReplyAnswerKind.TRY_AGAIN, code="CONSULTANT_UNKNOWN"),
        )
        forwarded.run()

        assert open_codes(forwarded) == []
        received = [s for s in forwarded.sent_subjects() if s.startswith("Timesheet received")]
        assert len(received) == 2  # the first reading, and the one that placed it


# --- QuickBooks that cannot be asked ---


class TestQuickBooksCannotBeAskedForTheCopy:
    def test_the_last_copy_carries_on_and_kevin_is_told_once(self, qbo: ScenarioEnv) -> None:
        qbo.run()  # yesterday's copy
        qbo.today = date(2026, 9, 9)
        qbo.accounting.fail_with = AccountingFailed("service unavailable")

        qbo.run()
        qbo.run()

        reviews = [r for r in qbo.store.open_reviews() if r.code == "QUICKBOOKS_FAILED"]
        assert len(reviews) == 1
        assert "the copy I took on 2026-09-08" in reviews[0].message
        emails = [e for e in qbo.sender.sent_emails() if "engagements in QuickBooks" in e.subject]
        assert len(emails) == 1
        assert '"try again"' in emails[0].body
        # The daily look is not counted as done on yesterday's copy.
        assert qbo.store.get_state(LAST_EXPECTED_CHECK_KEY) == "2026-09-08"

    def test_mail_is_still_read_from_the_last_copy(self, qbo: ScenarioEnv) -> None:
        qbo.run()
        qbo.today = date(2026, 9, 9)
        qbo.accounting.fail_with = AccountingFailed("service unavailable")
        qbo.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END))
        qbo.run()

        assert qbo.the_item().consultant == "Priya Shah"
        assert "UNKNOWN_SENDER" not in open_codes(qbo)

    def test_it_closes_by_itself_when_quickbooks_answers(self, qbo: ScenarioEnv) -> None:
        qbo.run()
        qbo.today = date(2026, 9, 9)
        qbo.accounting.fail_with = AccountingFailed("service unavailable")
        qbo.run()

        qbo.accounting.fail_with = None
        report = qbo.run()

        assert "QUICKBOOKS_FAILED" not in open_codes(qbo)
        assert any("up to date" in line for line in report.lines)
        assert qbo.store.get_state(LAST_EXPECTED_CHECK_KEY) == "2026-09-09"

    def test_no_copy_yet_means_the_spreadsheet(self, qbo: ScenarioEnv) -> None:
        qbo.workbook = default_workbook()
        qbo.accounting.fail_with = AccountingFailed("service unavailable")
        qbo.run()

        (review,) = [r for r in qbo.store.open_reviews() if r.code == "QUICKBOOKS_FAILED"]
        assert "the engagement list spreadsheet" in review.message

    def test_a_dead_connection_says_how_to_reconnect(self, qbo: ScenarioEnv) -> None:
        qbo.accounting.fail_with = AccountingNeedsReconnect("the refresh token has expired")
        qbo.run()

        (review,) = [r for r in qbo.store.open_reviews() if r.code == "QUICKBOOKS_RECONNECT"]
        assert "the refresh token has expired" in review.message
        email = next(e for e in qbo.sender.sent_emails() if "engagements" in e.subject)
        assert "fops qbo-connect" in email.body

    def test_try_again_takes_a_copy_on_the_next_run_whatever_the_date(
        self, qbo: ScenarioEnv
    ) -> None:
        qbo.run()
        qbo.today = date(2026, 9, 9)
        qbo.accounting.fail_with = AccountingFailed("service unavailable")
        qbo.run()
        qbo.accounting.fail_with = None
        qbo.run()  # fixed: today's copy taken, review closed
        calls = qbo.accounting.calls["engagements"]

        # Kevin's reply arrives after it closed: nothing left to answer, no error.
        kevin_says(
            qbo,
            "Needs your review: the engagements",
            "try again",
            answer(ReplyAnswerKind.TRY_AGAIN, code="QUICKBOOKS_FAILED"),
        )
        qbo.run()
        assert qbo.accounting.calls["engagements"] == calls

    def test_try_again_while_it_is_open_forces_a_copy(self, qbo: ScenarioEnv) -> None:
        qbo.run()
        qbo.today = date(2026, 9, 9)
        qbo.accounting.fail_with = AccountingFailed("service unavailable")
        qbo.run()
        kevin_says(
            qbo,
            "Needs your review: the engagements",
            "try again",
            answer(ReplyAnswerKind.TRY_AGAIN, code="QUICKBOOKS_FAILED"),
        )
        qbo.accounting.fail_with = None
        qbo.run()

        assert "QUICKBOOKS_FAILED" not in open_codes(qbo)

    def test_ignore_holds_while_quickbooks_stays_down(self, qbo: ScenarioEnv) -> None:
        """Kevin said ignore; the next failed run must not ask him again. The
        next outage, after QuickBooks has answered in between, is new."""
        qbo.accounting.fail_with = AccountingFailed("service unavailable")
        qbo.workbook = default_workbook()
        qbo.run()
        kevin_says(
            qbo,
            "Needs your review: the engagements",
            "ignore",
            answer(ReplyAnswerKind.IGNORE, code="QUICKBOOKS_FAILED"),
        )
        qbo.run()
        qbo.run()

        assert "QUICKBOOKS_FAILED" not in open_codes(qbo)
        told = [s for s in qbo.sent_subjects() if "engagements in QuickBooks" in s]
        assert len(told) == 1

        qbo.accounting.fail_with = None
        qbo.run()
        qbo.today = date(2026, 9, 9)
        qbo.accounting.fail_with = AccountingFailed("down again")
        qbo.run()
        assert "QUICKBOOKS_FAILED" in open_codes(qbo)

    def test_ignore_closes_it(self, qbo: ScenarioEnv) -> None:
        qbo.accounting.fail_with = AccountingFailed("service unavailable")
        qbo.workbook = default_workbook()
        qbo.run()
        kevin_says(
            qbo,
            "Needs your review: the engagements",
            "ignore",
            answer(ReplyAnswerKind.IGNORE, code="QUICKBOOKS_FAILED"),
        )
        qbo.run()

        assert "QUICKBOOKS_FAILED" not in open_codes(qbo)


# --- rates taken when the timesheet is read ---


class TestRatesForTheEngagementInHand:
    def test_a_waiting_item_takes_the_rate_quickbooks_has_now(self, qbo: ScenarioEnv) -> None:
        """August was expected at $140; Kevin raised it in QuickBooks before the
        timesheet came. The invoice uses what QuickBooks says when it is read."""
        qbo.run()
        assert qbo.the_item().snapshot.bill_rate_cents == 14_000
        qbo.accounting.rates[("Priya Shah", "Acme Corp")] = EngagementRates(
            ref="42",
            bill_rate_cents=15_000,
            pay_rate_cents=10_000,
            payee="Priya Shah",
            payee_ref="7",
        )
        qbo.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END))
        qbo.run()

        assert qbo.the_item().snapshot.bill_rate_cents == 15_000

    def test_quickbooks_down_holds_the_invoice_and_says_so(self, qbo: ScenarioEnv) -> None:
        qbo.run()
        qbo.accounting.fail_with = AccountingFailed("service unavailable")
        qbo.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END))
        qbo.run()

        item = qbo.the_item()
        assert item.status is ItemStatus.NEEDS_REVIEW
        (review,) = qbo.store.reviews_for_item(item.id)
        assert review.code == "QUICKBOOKS_FAILED"
        assert "won't invoice this yet" in review.message
        [message_id] = qbo.store.message_ids_for_item(item.id)
        assert qbo.mailbox.folders[message_id] == NEEDS_REVIEW_FOLDER

    def test_it_carries_on_by_itself_once_quickbooks_answers(self, qbo: ScenarioEnv) -> None:
        qbo.run()
        qbo.accounting.fail_with = AccountingFailed("service unavailable")
        qbo.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END))
        qbo.run()

        qbo.accounting.fail_with = None
        qbo.run()

        assert qbo.the_item().status is ItemStatus.READY
        assert open_codes(qbo) == []

    def test_kevin_saying_try_again_does_not_let_it_through_unconfirmed(
        self, qbo: ScenarioEnv
    ) -> None:
        qbo.run()
        qbo.accounting.fail_with = AccountingFailed("service unavailable")
        qbo.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END))
        qbo.run()
        kevin_says(
            qbo,
            "Needs your review",
            "try again",
            answer(ReplyAnswerKind.TRY_AGAIN, code="QUICKBOOKS_FAILED"),
        )
        qbo.run()  # QuickBooks still down

        assert qbo.the_item().status is ItemStatus.NEEDS_REVIEW
        assert open_codes(qbo) == ["QUICKBOOKS_FAILED"]

    def test_ignore_drops_the_timesheet(self, qbo: ScenarioEnv) -> None:
        qbo.run()
        qbo.accounting.fail_with = AccountingFailed("service unavailable")
        qbo.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END))
        qbo.run()
        kevin_says(
            qbo,
            "Needs your review",
            "ignore",
            answer(ReplyAnswerKind.IGNORE, code="QUICKBOOKS_FAILED"),
        )
        qbo.run()

        assert qbo.the_item().status is ItemStatus.IGNORED
