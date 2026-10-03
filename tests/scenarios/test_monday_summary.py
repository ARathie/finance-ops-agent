"""What the Monday summary lists beyond the basics (decision 64): the
duplicates filed quietly last week, and invoices sent but unpaid past their
due date."""

from datetime import date, timedelta

from finance_ops_agent.application.context import Mode
from finance_ops_agent.application.summary import DUPLICATES_FILED_KEY
from finance_ops_agent.domain.statuses import ItemStatus
from tests.scenarios.conftest import PRIYA, ScenarioEnv, engagement_row, reading

AUG_START, AUG_END = date(2026, 8, 1), date(2026, 8, 31)
MONDAY = date(2026, 9, 14)


def summary_body(env: ScenarioEnv) -> str:
    [email] = [
        email for email in env.sender.sent_emails() if email.subject.startswith("Weekly summary")
    ]
    return email.body


def section(body: str, title: str) -> str:
    """The lines of one section, up to the blank line that ends it."""
    start = body.index(title)
    end = body.find("\n\n", start)
    return body[start : end if end != -1 else len(body)]


class TestDuplicatesFiled:
    def test_the_same_file_again_is_listed(self, env: ScenarioEnv) -> None:
        env.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END))
        env.add_email(PRIYA, subject="Resending just in case")
        env.run()

        env.today = MONDAY
        env.run()

        listed = section(summary_body(env), "Duplicates filed")
        assert f"timesheet.pdf from {PRIYA} (the same file again)" in listed

    def test_the_same_hours_in_a_new_file_are_listed(self, env: ScenarioEnv) -> None:
        env.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END))
        env.add_email(
            PRIYA,
            subject="Timesheet again",
            attachment=("timesheet-copy.pdf", b"ANOTHER SCAN"),
            scripted_reading=reading(AUG_START, AUG_END),
        )
        env.run()
        assert len(env.store.list_items()) == 1

        env.today = MONDAY
        env.run()

        listed = section(summary_body(env), "Duplicates filed")
        assert "Priya Shah at Acme Corp" in listed
        assert "timesheet-copy.pdf: the same dates, hours and approval again" in listed

    def test_none_when_there_were_none(self, env: ScenarioEnv) -> None:
        env.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END))
        env.today = MONDAY
        env.run()
        assert "Duplicates filed: none" in summary_body(env)

    def test_only_last_weeks_are_listed(self, env: ScenarioEnv) -> None:
        env.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END))
        env.add_email(PRIYA, subject="Resending just in case")
        env.run()

        env.today = MONDAY + timedelta(days=7)  # the Monday after next
        env.run()
        assert "Duplicates filed: none" in summary_body(env)

    def test_old_entries_are_dropped_as_new_ones_come(self, env: ScenarioEnv) -> None:
        env.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END))
        env.add_email(PRIYA, subject="Resending just in case")
        env.run()

        env.today = env.today + timedelta(days=30)
        env.add_email(PRIYA, subject="And once more")
        env.run()

        kept = env.store.get_state(DUPLICATES_FILED_KEY) or ""
        assert kept.count("the same file again") == 1
        assert env.today.isoformat() in kept


def sent_invoice(env: ScenarioEnv) -> None:
    env.mode = Mode.AUTO
    env.workbook.engagements[0] = engagement_row(2, **{"Send automatically": "yes"})
    env.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END))
    env.run()
    assert env.the_item().status is ItemStatus.INVOICE_SENT


class TestUnpaidPastDue:
    def test_an_unpaid_invoice_past_its_due_date_is_listed(self, env: ScenarioEnv) -> None:
        sent_invoice(env)
        [invoice] = env.store.invoices_for_item(env.the_item().id)
        monday = invoice.due_date + timedelta(days=7 - invoice.due_date.weekday())
        env.today = monday
        env.run()

        listed = section(summary_body(env), "Unpaid invoices past their due date")
        days = (monday - invoice.due_date).days
        assert f"{invoice.number} — Acme Corp" in listed
        assert f"due {invoice.due_date} ({days} days ago)" in listed

    def test_none_before_the_due_date(self, env: ScenarioEnv) -> None:
        sent_invoice(env)
        [invoice] = env.store.invoices_for_item(env.the_item().id)
        env.today = MONDAY
        assert invoice.due_date >= MONDAY
        env.run()
        assert "Unpaid invoices past their due date: none" in summary_body(env)

    def test_a_paid_invoice_is_not_listed(self, env: ScenarioEnv) -> None:
        sent_invoice(env)
        item_id = env.the_item().id
        [invoice] = env.store.invoices_for_item(item_id)
        env.accounting.paid.add(invoice.external_id)
        env.today = invoice.due_date + timedelta(days=7 - invoice.due_date.weekday())
        env.run()  # the paid check runs before the summary is written

        assert env.store.get_item(item_id).status is ItemStatus.CLIENT_PAID
        assert "Unpaid invoices past their due date: none" in summary_body(env)

    def test_left_out_when_the_agent_cannot_see_payments(self, env: ScenarioEnv) -> None:
        """Manual mode: every invoice would look unpaid, so the section is
        left out rather than wrong."""
        sent_invoice(env)
        [invoice] = env.store.invoices_for_item(env.the_item().id)
        env.accounting.can_look_up_invoices = False
        env.today = invoice.due_date + timedelta(days=7 - invoice.due_date.weekday())
        env.run()
        assert "Unpaid invoices past their due date" not in summary_body(env)
