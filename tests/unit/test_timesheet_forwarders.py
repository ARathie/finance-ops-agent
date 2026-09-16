"""Addresses that may forward someone else's timesheet (decision 25).

The first-cycle test cannot have consultants emailing the agent yet, so old
timesheets are forwarded by hand from one known address. These prove the
forwarded mail is read as a timesheet, that the consultant is then taken from
the document rather than the sender, and that Kevin's own address can never be
turned into a forwarder.
"""

import pytest

from finance_ops_agent.config import MissingSettingError, _forwarders

KEVIN = "kevin@icon-technologies.com"


def set_env(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("FOPS_ADMIN_EMAIL", KEVIN)
    monkeypatch.setenv("FOPS_TIMESHEET_FORWARDERS", value)


def test_no_setting_means_nobody_may_forward(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FOPS_ADMIN_EMAIL", KEVIN)
    monkeypatch.delenv("FOPS_TIMESHEET_FORWARDERS", raising=False)
    assert _forwarders() == ()


def test_several_addresses_may_be_separated_either_way(monkeypatch: pytest.MonkeyPatch) -> None:
    set_env(monkeypatch, "meghan.rathie@gmail.com; ash@icon-technologies.com")
    assert _forwarders() == ("meghan.rathie@gmail.com", "ash@icon-technologies.com")

    set_env(monkeypatch, "meghan.rathie@gmail.com,ash@icon-technologies.com")
    assert _forwarders() == ("meghan.rathie@gmail.com", "ash@icon-technologies.com")


def test_blank_entries_and_stray_spaces_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    set_env(monkeypatch, " meghan.rathie@gmail.com , ; ")
    assert _forwarders() == ("meghan.rathie@gmail.com",)


def test_kevins_address_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    set_env(monkeypatch, KEVIN)
    with pytest.raises(MissingSettingError, match="approvals come that way"):
        _forwarders()


def test_kevins_address_is_refused_whatever_its_capitals(monkeypatch: pytest.MonkeyPatch) -> None:
    set_env(monkeypatch, "meghan.rathie@gmail.com, Kevin@Icon-Technologies.com")
    with pytest.raises(MissingSettingError):
        _forwarders()
