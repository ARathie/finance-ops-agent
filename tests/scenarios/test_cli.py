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


class TestTheEngagementsCommand:
    """Moving the engagement list off a file and into the agent's own store
    (decision 40)."""

    def settings(self, monkeypatch: pytest.MonkeyPatch, data: Path) -> None:
        for name, value in (
            ("FOPS_TIMEZONE", "America/New_York"),
            ("FOPS_ADMIN_EMAIL", "kevin@icon-technologies.com"),
            ("FOPS_AGENT_MAILBOX", "jay@icon-technologies.com"),
            ("FOPS_ENGAGEMENT_LIST", str(FIXTURES / "engagements")),
            ("FOPS_DATA_DIR", str(data)),
            ("FOPS_TIMESHEET_FORWARDERS", ""),
        ):
            monkeypatch.setenv(name, value)

    def test_it_says_where_the_list_is_read_from(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self.settings(monkeypatch, tmp_path)

        assert main(["engagements"]) == 0
        before = capsys.readouterr().out
        assert "engagements" in before  # the fixture folder
        assert "Priya Shah at Acme Corp" in before

        assert main(["engagements", "import"]) == 0
        capsys.readouterr()

        assert main(["engagements"]) == 0
        after = capsys.readouterr().out
        assert "the agent's own store" in after
        assert "Priya Shah at Acme Corp" in after

    def test_forget_puts_it_back_on_the_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self.settings(monkeypatch, tmp_path)
        assert main(["engagements", "import"]) == 0
        capsys.readouterr()

        assert main(["engagements", "forget"]) == 0
        assert "will read" in capsys.readouterr().out

        assert main(["engagements"]) == 0
        assert "the agent's own store" not in capsys.readouterr().out

    def test_a_workbook_with_problems_is_not_imported(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Importing one the agent would refuse to bill from would only move
        the problem somewhere harder to see."""
        broken = tmp_path / "broken"
        broken.mkdir()
        for name in ("clients", "consultants", "vendors", "engagements"):
            text = (FIXTURES / "engagements" / f"{name}.csv").read_text()
            if name == "clients":
                text = text.replace(",AC,", ",,")  # no invoice code
            (broken / f"{name}.csv").write_text(text)
        self.settings(monkeypatch, tmp_path)

        assert main(["engagements", "import", "--from", str(broken)]) == 1
        out = capsys.readouterr().out
        assert "nothing was imported" in out
        assert "Invoice code" in out

        assert main(["engagements"]) == 0
        assert "the agent's own store" not in capsys.readouterr().out
