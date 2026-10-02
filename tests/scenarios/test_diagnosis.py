"""Diagnosis says what is stuck and why, and touches nothing (decision 55).

Each case is one the first live cycle hit by hand: an invoice in the agent's
records that QuickBooks does not have, a number QuickBooks already holds, an
email in the inbox the agent will not read, and items left over from testing.
"""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

import pytest

from finance_ops_agent.application.context import Mode
from finance_ops_agent.application.diagnosis import (
    FindingKind,
    Looking,
    check_inbox,
    check_items,
    check_recorded_invoices,
    describe_item,
    diagnose_everything,
    explain_invoice_number,
)
from finance_ops_agent.application.run import MAILBOX_POSITION_KEY
from finance_ops_agent.domain.engagements import parse_workbook
from finance_ops_agent.domain.messages import InboxEntry
from finance_ops_agent.ports.accounting import AccountingFailed, InvoiceLookup
from finance_ops_agent.ports.looking import StoreToLookAt
from tests.scenarios.conftest import PRIYA, ScenarioEnv, engagement_row, reading

AUG_START, AUG_END = date(2026, 8, 1), date(2026, 8, 31)
NUMBER = "083126AC-PS"


def looking(env: ScenarioEnv, inbox: Any = None) -> Looking:
    return Looking(
        store=env.store,
        accounting=env.accounting,
        inbox=env.mailbox if inbox is None else inbox,
        mailbox_position=env.store.get_state(MAILBOX_POSITION_KEY),
        mail_start_date=date(2026, 9, 1),
    )


def kinds(findings: list[Any]) -> list[FindingKind]:
    return [finding.kind for finding in findings]


def invoice_sent(env: ScenarioEnv) -> int:
    """Ask first, approved: the item is invoice_sent with one invoice."""
    env.mode = Mode.ASK_FIRST
    env.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END))
    env.run()
    approval = next(s for s in env.sent_subjects() if s.startswith("Approve?"))
    env.reply_from_kevin(approval, "approve")
    env.run()
    return env.the_item().id


class TestInvoicesAgainstQuickBooks:
    def test_an_invoice_quickbooks_does_not_have_is_named_with_the_fix(
        self, env: ScenarioEnv
    ) -> None:
        """Invoice 153: made in the sandbox, asked about in production."""
        item_id = invoice_sent(env)
        env.accounting.invoices.clear()  # QuickBooks has never heard of it

        [finding] = check_recorded_invoices(looking(env))

        assert finding.kind is FindingKind.INVOICE_NOT_IN_ACCOUNTING
        assert finding.item_id == item_id
        assert NUMBER in finding.what
        assert "paid invoices fails" in finding.what
        assert f"fops forget {item_id} --force" in finding.next_step

    def test_invoices_quickbooks_has_are_not_findings(self, env: ScenarioEnv) -> None:
        invoice_sent(env)
        assert check_recorded_invoices(looking(env)) == []

    def test_quickbooks_that_cannot_be_asked_is_said_once(self, env: ScenarioEnv) -> None:
        invoice_sent(env)
        env.accounting.fail_with = AccountingFailed("the connection timed out")

        assert kinds(check_recorded_invoices(looking(env))) == [FindingKind.ACCOUNTING_UNREACHABLE]


class TestWhoHoldsANumber:
    def number_taken(self, env: ScenarioEnv, holder_item: int | None) -> None:
        env.mode = Mode.ASK_FIRST
        env.accounting.taken_numbers.add(NUMBER)
        env.accounting.other_invoices["2"] = InvoiceLookup(
            external_id="2",
            number=NUMBER,
            total_cents=2_184_000,
            balance_cents=2_184_000,
            customer="Acme Corporation",
            item_id=holder_item,
            issued="2026-09-01",
        )
        env.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END))
        env.run()

    def test_the_agents_own_invoice_for_a_forgotten_item(self, env: ScenarioEnv) -> None:
        """Manoj's 083126MT-MK: made for item 30, which `fops forget` removed."""
        self.number_taken(env, holder_item=30)

        findings = diagnose_everything(looking(env), None)

        held = next(f for f in findings if f.kind is FindingKind.NUMBER_HELD_BY_FORGOTTEN_ITEM)
        assert "item 30" in held.what and "fops forget" in held.what
        assert "delete, not void" in held.next_step
        assert f"{NUMBER}-revised" in held.next_step
        assert held.item_id == env.the_item().id

    def test_an_invoice_made_by_hand(self, env: ScenarioEnv) -> None:
        self.number_taken(env, holder_item=None)

        report = describe_item(looking(env), env.the_item().id)

        assert FindingKind.NUMBER_HELD_BY_HAND in kinds(report.findings)
        assert any("QUICKBOOKS_FAILED" in fact for fact in report.facts)

    def test_a_free_number_is_said_to_be_free(self, env: ScenarioEnv) -> None:
        findings = explain_invoice_number(looking(env), "083126AC-ZZ")
        assert kinds(findings) == [FindingKind.NUMBER_FREE]
        assert not findings[0].needs_attention


@dataclass
class StubInbox:
    entries: list[InboxEntry] = field(default_factory=list)

    def inbox_listing(self, position: str | None) -> list[InboxEntry]:
        return self.entries


def entry(
    message_id: str, after_position: bool = True, on_or_after_start: bool = True
) -> InboxEntry:
    return InboxEntry(
        message_id=message_id,
        from_address=PRIYA,
        subject="Fwd: James Mercado Aug timesheet",
        received_at=datetime(2026, 8, 28, 9, 0, tzinfo=UTC),
        after_position=after_position,
        on_or_after_start=on_or_after_start,
    )


class TestTheInbox:
    def test_an_email_already_handled_names_its_item_and_the_order_that_works(
        self, env: ScenarioEnv
    ) -> None:
        env.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END))
        env.run()
        env.mailbox.folders.clear()  # dragged back into the inbox

        [finding] = check_inbox(looking(env))

        assert finding.kind is FindingKind.EMAIL_ALREADY_HANDLED
        assert finding.item_id == env.the_item().id
        assert f"fops forget {env.the_item().id}` first" in finding.next_step

    def test_an_email_before_the_start_date(self, env: ScenarioEnv) -> None:
        inbox = StubInbox([entry("<old@example>", on_or_after_start=False)])

        [finding] = check_inbox(looking(env, inbox))

        assert finding.kind is FindingKind.EMAIL_BEFORE_START_DATE
        assert "2026-09-01" in finding.what  # the start date that rules it out
        assert "--since 2026-08-28" in finding.next_step

    def test_an_email_the_agent_already_read_past(self, env: ScenarioEnv) -> None:
        inbox = StubInbox([entry("<behind@example>", after_position=False)])

        [finding] = check_inbox(looking(env, inbox))

        assert finding.kind is FindingKind.EMAIL_ALREADY_READ_PAST
        assert "out of the inbox and back in" in finding.next_step

    def test_new_mail_is_fine(self, env: ScenarioEnv) -> None:
        env.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END))

        [finding] = check_inbox(looking(env))

        assert finding.kind is FindingKind.EMAIL_WILL_BE_READ
        assert not finding.needs_attention


class TestItems:
    def test_an_item_whose_client_is_not_on_the_list(self, env: ScenarioEnv) -> None:
        """The old "iStream" and "MasTec" items, from before the names changed."""
        env.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END))
        env.run()
        renamed = parse_workbook(env.workbook)
        renamed.engagements[:] = [
            row.__class__(**{**row.__dict__, "client": "Acme Corporation"})
            for row in renamed.engagements
        ]

        findings = check_items(looking(env), renamed)

        [finding] = [f for f in findings if f.kind is FindingKind.ITEM_NOT_ON_ENGAGEMENT_LIST]
        assert finding.item_id == env.the_item().id
        assert "Priya Shah at Acme Corp" in finding.what

    def test_many_months_waiting_points_at_the_start_date(self, env: ScenarioEnv) -> None:
        """Subramanian: sixteen months waiting, because the start date was 2025."""
        env.workbook.engagements[0] = engagement_row(
            2, **{"Start date": "2026-03-01", "Rates from": "2026-03-01"}
        )
        env.run()

        findings = check_items(looking(env), parse_workbook(env.workbook))

        [finding] = [f for f in findings if f.kind is FindingKind.MANY_PERIODS_WAITING]
        assert "Priya Shah at Acme Corp" in finding.what
        assert "2026-03-01" in finding.what
        assert "start date" in finding.next_step


class ReadOnlyStore:
    """The store, with every write turned into a test failure."""

    def __init__(self, store: StoreToLookAt) -> None:
        self._store = store

    def __getattr__(self, name: str) -> Any:
        allowed = {name for name in StoreToLookAt.__dict__ if not name.startswith("_")}
        if name not in allowed:
            pytest.fail(f"diagnosis called {name}, which is not a read")
        return getattr(self._store, name)


class ReadOnlyAccounting:
    def __init__(self, accounting: Any) -> None:
        self._accounting = accounting

    def __getattr__(self, name: str) -> Any:
        if name not in {"can_look_up_invoices", "invoice_lookup", "invoices_numbered"}:
            pytest.fail(f"diagnosis called {name} on the accounting system")
        return getattr(self._accounting, name)


def test_diagnosis_reads_and_never_writes(env: ScenarioEnv) -> None:
    """Belt and braces: mypy already refuses a write, and this proves it at run time
    across every check, on records with something wrong in each of them."""
    item_id = invoice_sent(env)
    env.accounting.invoices.clear()
    env.mailbox.folders.clear()
    before = (
        env.store.list_items(),
        env.store.outgoing_records(),
        env.store.open_reviews(),
        env.store.get_state(MAILBOX_POSITION_KEY),
    )
    guarded = Looking(
        store=ReadOnlyStore(env.store),
        accounting=ReadOnlyAccounting(env.accounting),
        inbox=env.mailbox,
        mailbox_position=env.store.get_state(MAILBOX_POSITION_KEY),
    )

    diagnose_everything(guarded, parse_workbook(env.workbook))
    describe_item(guarded, item_id)
    explain_invoice_number(guarded, NUMBER)

    after = (
        env.store.list_items(),
        env.store.outgoing_records(),
        env.store.open_reviews(),
        env.store.get_state(MAILBOX_POSITION_KEY),
    )
    assert after == before
    assert env.mailbox.folders == {}  # nothing filed


def test_the_guard_itself_catches_a_write(env: ScenarioEnv) -> None:
    """A guard that let everything through would make the test above prove nothing."""
    with pytest.raises(pytest.fail.Exception):
        ReadOnlyStore(env.store).set_state("anything", "x")
    with pytest.raises(pytest.fail.Exception):
        ReadOnlyAccounting(env.accounting).cancel_invoice("ext-1")
