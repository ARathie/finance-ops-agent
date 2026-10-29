"""A salaried employee, through whole runs (decision 69).

Dana Cruz is an Icon employee on a fixed salary. Her engagement has a bill
rate and no pay rate. She may report her hours in the email itself -- "168
hours were worked in August" -- and those are trusted as she wrote them; or
she may send a timesheet, which goes the way every other timesheet does. The
client is invoiced either way. What Kevin gets instead of a payment
instruction is a reminder to pay her salary, with no amount on it.
"""

from datetime import date

from finance_ops_agent.application.run import Mode
from finance_ops_agent.domain.reading import (
    Approval,
    ApprovalKind,
    Confidence,
    DailyEntry,
    ReadField,
    TimesheetReading,
)
from finance_ops_agent.domain.statuses import ItemStatus
from tests.scenarios.conftest import (
    DANA,
    PRIYA,
    ScenarioEnv,
    consultant_row,
    engagement_row,
    reading,
)

AUG_START, AUG_END = date(2026, 8, 1), date(2026, 8, 31)
ONE_LINER = "168 hours were worked in August."
FORWARDER = "tester@icon-technologies.com"


def hours_email(
    total_hundredths: int | None = 16_800,
    month: date | None = AUG_START,
    consultant: str | None = None,
    confidence: Confidence = Confidence.HIGH,
) -> TimesheetReading:
    """What the reader makes of a one-line email: a month and a number."""
    return TimesheetReading(
        consultant_name=ReadField[str](
            value=consultant, quote=consultant, confidence=Confidence.HIGH
        ),
        client_name=ReadField[str](confidence=Confidence.HIGH),
        end_client_name=ReadField[str](confidence=Confidence.HIGH),
        period_start=ReadField[date](confidence=Confidence.HIGH),
        period_end=ReadField[date](confidence=Confidence.HIGH),
        stated_month_start=ReadField[date](value=month, quote="August", confidence=confidence),
        daily_entries=ReadField[list[DailyEntry]](value=[], confidence=Confidence.HIGH),
        stated_total_hours_hundredths=ReadField[int](
            value=total_hundredths, quote="168 hours", confidence=confidence
        ),
        approval=ReadField[Approval](
            value=Approval(kind=ApprovalKind.NONE), confidence=Confidence.HIGH
        ),
    )


def dana_on_a_salary(env: ScenarioEnv) -> None:
    env.workbook.consultants[1] = consultant_row(
        3,
        Consultant="Dana Cruz",
        Email=DANA,
        Type="employee",
        **{"Other names": "", "Paid by": "salary", "Pay timing (days)": ""},
    )
    env.workbook.engagements[1] = engagement_row(3, Consultant="Dana Cruz", **{"Pay rate": ""})


def dana_reports_in_the_email(env: ScenarioEnv, body: str = ONE_LINER) -> None:
    dana_on_a_salary(env)
    env.email_readings[body] = hours_email()
    env.add_email(DANA, subject="August hours", attachment=None, body=body)
    env.run()


def dana_item(env: ScenarioEnv):  # type: ignore[no-untyped-def]
    [item] = [item for item in env.store.list_items() if item.consultant == "Dana Cruz"]
    return item


def nothing_received(env: ScenarioEnv) -> bool:
    """Only the records the daily look makes for ended periods: no timesheet."""
    return all(item.status is ItemStatus.WAITING_FOR_TIMESHEET for item in env.store.list_items())


def open_codes(env: ScenarioEnv) -> set[str]:
    return {review.code for review in env.store.open_reviews()}


class TestHoursInTheEmail:
    def test_a_one_line_email_is_trusted_and_invoiced(self, env: ScenarioEnv) -> None:
        dana_reports_in_the_email(env)

        item = dana_item(env)
        assert item.status is ItemStatus.READY
        assert (item.period.start, item.period.end) == (AUG_START, AUG_END)
        assert item.approved_hours is not None and item.approved_hours.hundredths == 16_800
        assert item.invoice_amount is not None and item.invoice_amount.cents == 2_352_000
        assert item.snapshot.salaried
        # No attachment, and no approval asked for: neither is a question.
        assert not open_codes(env) & {"NO_ATTACHMENT", "NO_APPROVAL", "NOT_SURE"}

    def test_kevin_is_shown_her_words_for_his_records(self, env: ScenarioEnv) -> None:
        dana_reports_in_the_email(env)

        [details] = [
            email
            for email in env.sender.sent_emails()
            if email.subject.startswith("Timesheet received: Dana Cruz")
        ]
        assert "168.00 hours" in details.subject
        assert "From the email itself (there is no timesheet)" in details.body
        assert "Approval: not needed" in details.body
        assert details.attachments == ()

    def test_the_client_gets_the_invoice_and_nothing_she_wrote(self, env: ScenarioEnv) -> None:
        dana_reports_in_the_email(env)

        [preview] = [
            email
            for email in env.sender.sent_emails()
            if email.subject.startswith("Dry run — would invoice Acme Corp — Dana Cruz")
        ]
        assert [a.filename for a in preview.attachments] == ["invoice-DRAFT.pdf"]
        assert "(168.00 hours)." in preview.body
        assert "approved timesheet" not in preview.body

    def test_kevin_is_reminded_to_pay_the_salary_not_told_an_amount(self, env: ScenarioEnv) -> None:
        dana_reports_in_the_email(env)

        subjects = env.sent_subjects()
        assert "Pay salary: Dana Cruz — Aug 2026" in subjects
        assert not any(s.startswith("Payment due") and "Dana Cruz" in s for s in subjects)
        [reminder] = [e for e in env.sender.sent_emails() if e.subject.startswith("Pay salary")]
        assert "$" not in reminder.body
        assert env.store.payment_instructions_for_item(dana_item(env).id) == []

    def test_the_tracking_sheet_says_salary(self, env: ScenarioEnv) -> None:
        from finance_ops_agent.application.summary import tracking_rows

        dana_reports_in_the_email(env)

        [row] = [row for row in tracking_rows(env.deps()) if row.consultant == "Dana Cruz"]
        assert (row.pay_rate, row.amount_owed) == ("salary", "salary")
        assert "salary" in row.owed_to

    def test_the_same_words_again_are_a_duplicate(self, env: ScenarioEnv) -> None:
        dana_reports_in_the_email(env)
        sent = len(env.sender.sent_emails())

        env.add_email(DANA, subject="Fwd: August hours", attachment=None, body=ONE_LINER)
        report = env.run()

        assert report.duplicates_filed == 1
        assert len(env.sender.sent_emails()) == sent
        assert len([i for i in env.store.list_items() if i.consultant == "Dana Cruz"]) == 1

    def test_different_hours_for_the_same_month_are_a_correction(self, env: ScenarioEnv) -> None:
        dana_reports_in_the_email(env)

        env.email_readings["Sorry, 160 hours in August."] = hours_email(16_000)
        env.add_email(DANA, attachment=None, body="Sorry, 160 hours in August.")
        env.run()

        assert "CORRECTION" in open_codes(env)

    def test_unusually_many_hours_are_still_a_question(self, env: ScenarioEnv) -> None:
        dana_on_a_salary(env)
        env.email_readings["400 hours in August"] = hours_email(40_000)
        env.add_email(DANA, attachment=None, body="400 hours in August")
        env.run()

        assert dana_item(env).status is ItemStatus.NEEDS_REVIEW
        assert "HOURS_UNUSUAL" in open_codes(env)

    def test_an_email_with_no_hours_in_it_is_asked_about(self, env: ScenarioEnv) -> None:
        dana_on_a_salary(env)
        env.add_email(DANA, attachment=None, body="Hi Jay, hope you are well.")
        env.run()

        assert "NO_ATTACHMENT" in open_codes(env)
        assert nothing_received(env)
        [review] = [r for r in env.store.open_reviews() if r.code == "NO_ATTACHMENT"]
        assert "couldn't find the hours in the email itself" in review.message

    def test_ask_first_shows_kevin_her_words_then_sends(self, env: ScenarioEnv) -> None:
        env.mode = Mode.ASK_FIRST
        dana_reports_in_the_email(env)

        [approval] = [e for e in env.sender.sent_emails() if e.subject.startswith("Approve?")]
        assert '"168 hours / August"' in approval.body
        assert [a.filename for a in approval.attachments] == ["invoice-083126AC-DC.pdf"]

        env.reply_from_kevin(approval.subject, "approve")
        env.run()

        item = dana_item(env)
        assert item.status is ItemStatus.INVOICE_SENT
        [billing] = [e for e in env.sender.sent_emails() if e.to == ("ap@acme.example",)]
        assert [a.filename for a in billing.attachments] == ["invoice-083126AC-DC.pdf"]
        assert "kevin@icon-technologies.com" in billing.cc
        [reminder] = [e for e in env.sender.sent_emails() if e.subject.startswith("Pay salary")]
        assert "invoice 083126AC-DC" in reminder.body

    def test_automatic_mode_never_sends_her_words_without_asking(self, env: ScenarioEnv) -> None:
        env.mode = Mode.AUTO
        dana_on_a_salary(env)
        env.workbook.engagements[1] = engagement_row(
            3, Consultant="Dana Cruz", **{"Pay rate": "", "Send automatically": "yes"}
        )
        env.email_readings[ONE_LINER] = hours_email()
        env.add_email(DANA, attachment=None, body=ONE_LINER)
        env.run()

        assert dana_item(env).status is ItemStatus.WAITING_FOR_APPROVAL
        assert not any(e.to == ("ap@acme.example",) for e in env.sender.sent_emails())


class TestOnlyASalariedEmployee:
    def test_a_contractor_with_no_attachment_is_asked_about_as_before(
        self, env: ScenarioEnv
    ) -> None:
        dana_on_a_salary(env)
        env.add_email(PRIYA, attachment=None, body="168 hours in August")
        reader_emails = env.email_readings
        env.run()

        assert "NO_ATTACHMENT" in open_codes(env)
        assert nothing_received(env)
        assert reader_emails == {}  # nothing was scripted, and nothing was needed

    def test_nobody_salaried_means_no_email_is_read_for_hours(self, env: ScenarioEnv) -> None:
        env.add_email(DANA, attachment=None, body=ONE_LINER)
        env.email_readings[ONE_LINER] = hours_email()
        deps = env.deps()
        from finance_ops_agent.application.run import run_once

        run_once(deps)

        assert deps.reader.hours_emails_read == []  # type: ignore[attr-defined]
        assert "NO_ATTACHMENT" in open_codes(env)

    def test_forwarded_hours_naming_her_are_hers(self, env: ScenarioEnv) -> None:
        env.forwarders = (FORWARDER,)
        dana_on_a_salary(env)
        forwarded = "From: Dana Cruz\n168 hours were worked in August."
        env.email_readings[forwarded] = hours_email(consultant="Dana Cruz")
        env.add_email(FORWARDER, attachment=None, body=forwarded)
        env.run()

        assert dana_item(env).status is ItemStatus.READY

    def test_forwarded_hours_naming_a_contractor_are_not_trusted(self, env: ScenarioEnv) -> None:
        env.forwarders = (FORWARDER,)
        dana_on_a_salary(env)
        forwarded = "From: Priya Shah\n168 hours were worked in August."
        env.email_readings[forwarded] = hours_email(consultant="Priya Shah")
        env.add_email(FORWARDER, attachment=None, body=forwarded)
        env.run()

        assert "NO_ATTACHMENT" in open_codes(env)
        assert nothing_received(env)


class TestWithATimesheet:
    def test_her_timesheet_goes_the_usual_way_approval_and_all(self, env: ScenarioEnv) -> None:
        dana_on_a_salary(env)
        env.add_email(
            DANA,
            scripted_reading=reading(
                AUG_START, AUG_END, 16_800, consultant="Dana Cruz", approved=False
            ),
        )
        env.run()

        item = dana_item(env)
        assert item.status is ItemStatus.NEEDS_REVIEW
        assert "NO_APPROVAL" in open_codes(env)

    def test_an_approved_timesheet_is_invoiced_and_attached(self, env: ScenarioEnv) -> None:
        dana_on_a_salary(env)
        env.add_email(
            DANA,
            attachment=("dana-august.pdf", b"PDF"),
            scripted_reading=reading(AUG_START, AUG_END, 16_800, consultant="Dana Cruz"),
        )
        env.run()

        item = dana_item(env)
        assert item.status is ItemStatus.READY
        [preview] = [e for e in env.sender.sent_emails() if e.subject.startswith("Dry run")]
        assert "dana-august.pdf" in [a.filename for a in preview.attachments]
        assert "approved timesheet" in preview.body
        assert "Pay salary: Dana Cruz — Aug 2026" in env.sent_subjects()


class TestWithQuickBooksDeciding:
    """FOPS_ENGAGEMENTS=quickbooks: the product has no vendor and no cost,
    and the employee's address is an `Email:` line on it."""

    def test_no_vendor_on_the_product_means_a_salary(self, env: ScenarioEnv) -> None:
        from finance_ops_agent.domain.engagements import RawWorkbook
        from finance_ops_agent.ports.accounting import (
            AccountingEngagement,
            AccountingParty,
            EngagementRates,
        )

        env.engagements_from = "quickbooks"
        env.workbook = RawWorkbook(clients=[], consultants=[], vendors=[], engagements=[])
        env.accounting.live = [
            AccountingEngagement(
                ref="44",
                consultant="Dana Cruz",
                client="Acme Corp",
                bill_rate_cents=14_000,
                pay_rate_cents=None,
                payee="",
                payee_ref="",
                notes="",  # the purchase box unticked: no description to write in
            )
        ]
        # Her address is on her employee record (decision 70).
        env.accounting.employee_records = [
            AccountingParty(ref="e1", name="Dana Cruz", email=DANA, payment_terms_days=None)
        ]
        env.accounting.customers["Acme Corp"] = AccountingParty(
            ref="58",
            name="Acme Corp",
            company="Acme Corporation",
            email="ap@acme.example",
            payment_terms_days=30,
            notes="Invoice code: AC",
        )
        env.accounting.rates[("Dana Cruz", "Acme Corp")] = EngagementRates(
            ref="44", bill_rate_cents=14_000, pay_rate_cents=None, payee=""
        )
        env.email_readings[ONE_LINER] = hours_email()
        env.add_email(DANA, attachment=None, body=ONE_LINER)
        env.run()

        # No start date anywhere: last month is assumed (August, on Sep 8),
        # and remembered for next time.
        item = env.the_item()
        assert (item.period.start, item.period.end) == (AUG_START, AUG_END)
        assert item.status is ItemStatus.READY
        assert item.snapshot.salaried
        assert env.store.get_state("salaried_starts") == '{"44": "2026-08-01"}'
        assert item.snapshot.bill_rate_cents == 14_000
        assert env.store.open_reviews() == []
        assert "Pay salary: Dana Cruz — Aug 2026" in env.sent_subjects()

    def test_a_list_saying_salary_and_quickbooks_an_hourly_cost_is_asked(
        self, env: ScenarioEnv
    ) -> None:
        from finance_ops_agent.ports.accounting import EngagementRates

        dana_on_a_salary(env)
        env.accounting.rates[("Dana Cruz", "Acme Corp")] = EngagementRates(
            ref="44", bill_rate_cents=14_000, pay_rate_cents=9_000, payee="Dana Cruz LLC"
        )
        env.email_readings[ONE_LINER] = hours_email()
        env.add_email(DANA, attachment=None, body=ONE_LINER)
        env.run()

        item = dana_item(env)
        # Held, as on any rate disagreement: the open question keeps it from
        # being made ready (decision 38).
        assert item.status is not ItemStatus.READY
        assert not item.snapshot.salaried  # QuickBooks' answer is the one used
        assert item.snapshot.pay_rate_cents == 9_000
        [review] = [r for r in env.store.open_reviews() if r.code == "LIST_ROW_PROBLEM"]
        assert "the engagement list says they are paid a salary" in review.message


def test_the_same_words_next_month_are_next_months_hours(env: ScenarioEnv) -> None:
    """ "168 hours this month" can be word for word what she wrote last month."""
    dana_on_a_salary(env)
    env.workbook.engagements[1] = engagement_row(
        3,
        Consultant="Dana Cruz",
        **{"Pay rate": "", "Start date": "2026-07-01", "Rates from": "2026-07-01"},
    )
    same = "168 hours this month."
    env.email_readings[same] = hours_email(month=date(2026, 7, 1))
    env.add_email(
        DANA,
        subject="Hours",
        attachment=None,
        body=same,
        message_id="<july@example>",
        sent="Mon, 03 Aug 2026 09:00:00 +0000",
    )
    env.run()
    env.email_readings[same] = hours_email(month=AUG_START)
    env.add_email(DANA, subject="Hours", attachment=None, body=same, message_id="<aug@example>")
    env.run()

    months = sorted(
        item.period.start.month
        for item in env.store.list_items()
        if item.consultant == "Dana Cruz" and item.status is ItemStatus.READY
    )
    assert months == [7, 8]
