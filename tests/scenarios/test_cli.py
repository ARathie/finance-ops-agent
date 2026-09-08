"""`fops dry-run --fake` and `fops status` on the committed fixtures."""

from pathlib import Path

import pytest

from finance_ops_agent.cli.main import main

FIXTURES = Path(__file__).parent.parent / "fixtures" / "fake_run"


def test_dry_run_on_the_committed_fixtures(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(["dry-run", "--fake", "--fixtures", str(FIXTURES), "--data", str(tmp_path)])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "ready to invoice: Priya Shah at Acme Corp" in out
    assert "invoice 21,840.00, owed 15,600.00" in out  # the worked example
    assert "duplicate filed quietly" in out
    assert "UNKNOWN_SENDER" in out


def test_running_the_same_mailbox_twice_changes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = ["dry-run", "--fake", "--fixtures", str(FIXTURES), "--data", str(tmp_path)]
    main(args)
    first = capsys.readouterr().out.split("\n\n", 1)[1]  # everything after the run lines
    main(args)
    second = capsys.readouterr().out.split("\n\n", 1)[1]
    assert first == second


def test_status_reads_the_same_data_folder(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["dry-run", "--fake", "--fixtures", str(FIXTURES), "--data", str(tmp_path)])
    capsys.readouterr()
    exit_code = main(["status", "--data", str(tmp_path)])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Priya Shah at Acme Corp" in out
    assert "ready" in out


def test_dry_run_without_fake_is_refused(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["dry-run"]) == 2
