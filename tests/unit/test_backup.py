"""Backup and restore round-trip a populated data folder."""

import zipfile
from datetime import date
from pathlib import Path

import pytest

from finance_ops_agent.adapters.sqlite.store import SqliteStore, open_database
from finance_ops_agent.application.backup import (
    RestoreRefused,
    back_up,
    restore,
)
from finance_ops_agent.cli.main import main
from finance_ops_agent.domain.statuses import ItemStatus
from tests.contract.test_store import AUGUST, snapshot

TODAY = date(2026, 9, 9)


def populated(data_dir: Path) -> SqliteStore:
    """A data folder like a real one: database, files, tokens, and a lock."""
    store = SqliteStore(open_database(data_dir / "agent.db"), files_dir=data_dir / "files")
    item = store.create_item("Priya Shah", "Acme Corp", AUGUST, ItemStatus.RECEIVED, snapshot())
    store.change_status(item.id, ItemStatus.READY, {"why": "all checks passed"})
    store.save_file(b"%PDF-a stored invoice")
    store.set_state("mailbox_cursor", "a-delta-link")
    (data_dir / "qbo_tokens.json").write_text('{"realm_id": "123"}')
    (data_dir / "run.lock").write_text("4242\n")
    return store


class TestRoundTrip:
    def test_a_populated_folder_survives_backup_and_restore(self, tmp_path: Path) -> None:
        data = tmp_path / "data"
        data.mkdir()
        store = populated(data)
        item_id = store.list_items()[0].id
        audit_count = len(store.audit_entries(item_id))

        result = back_up(data, tmp_path / "backups", TODAY, store=store)
        assert result.path.name == "fops-backup-2026-09-09.zip"

        # Restore into a completely fresh folder and read everything back.
        restored_dir = tmp_path / "restored"
        count = restore(result.path, restored_dir)
        assert count == result.files

        restored = SqliteStore(
            open_database(restored_dir / "agent.db"), files_dir=restored_dir / "files"
        )
        item = restored.get_item(item_id)
        assert item.consultant == "Priya Shah"
        assert item.status is ItemStatus.READY
        assert len(restored.audit_entries(item_id)) == audit_count
        assert restored.get_state("mailbox_cursor") == "a-delta-link"
        [sha] = [path.name for path in (restored_dir / "files").iterdir()]
        assert restored.load_file(sha) == b"%PDF-a stored invoice"
        assert (restored_dir / "qbo_tokens.json").read_text() == '{"realm_id": "123"}'

    def test_the_lock_file_is_not_backed_up(self, tmp_path: Path) -> None:
        data = tmp_path / "data"
        data.mkdir()
        store = populated(data)
        result = back_up(data, tmp_path / "backups", TODAY, store=store)
        with zipfile.ZipFile(result.path) as archive:
            names = archive.namelist()
        # A lock belongs to a running process, not to the data.
        assert "run.lock" not in names
        assert "agent.db" in names

    def test_sqlite_sidecar_files_are_not_backed_up(self, tmp_path: Path) -> None:
        data = tmp_path / "data"
        data.mkdir()
        store = populated(data)
        # The store writes in WAL mode, so the sidecars are there to be skipped.
        assert (data / "agent.db-wal").exists()

        result = back_up(data, tmp_path / "backups", TODAY, store=store)
        with zipfile.ZipFile(result.path) as archive:
            names = archive.namelist()
        # The checkpoint folded them into agent.db; copying them would be
        # copying a half-written journal from a database that has moved on.
        assert not [name for name in names if name.endswith(("-wal", "-shm"))]
        assert "agent.db" in names


class TestRestoreSafety:
    def test_restoring_over_a_folder_with_things_in_it_is_refused(self, tmp_path: Path) -> None:
        data = tmp_path / "data"
        data.mkdir()
        store = populated(data)
        result = back_up(data, tmp_path / "backups", TODAY, store=store)

        with pytest.raises(RestoreRefused, match="not empty"):
            restore(result.path, data)

    def test_force_overwrites_deliberately(self, tmp_path: Path) -> None:
        data = tmp_path / "data"
        data.mkdir()
        store = populated(data)
        result = back_up(data, tmp_path / "backups", TODAY, store=store)
        assert restore(result.path, data, force=True) > 0

    def test_an_archive_that_tries_to_escape_the_folder_is_refused(self, tmp_path: Path) -> None:
        archive_path = tmp_path / "nasty.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("../escaped.txt", "somewhere it should not be")
        with pytest.raises(RestoreRefused, match="outside"):
            restore(archive_path, tmp_path / "data")
        assert not (tmp_path / "escaped.txt").exists()


def test_a_backup_without_a_checkpoint_can_lose_committed_data(tmp_path: Path) -> None:
    """Why back_up checkpoints: in WAL mode the .db file alone may be incomplete."""
    data = tmp_path / "data"
    data.mkdir()
    store = populated(data)
    item_id = store.list_items()[0].id

    # Deliberately skip the checkpoint, the way a plain folder copy would.
    careless = back_up(data, tmp_path / "careless", TODAY)
    restore(careless.path, tmp_path / "from-careless")
    careless_store = SqliteStore(
        open_database(tmp_path / "from-careless" / "agent.db"),
        files_dir=tmp_path / "from-careless" / "files",
    )
    with pytest.raises(KeyError):
        careless_store.get_item(item_id)

    # With the checkpoint, the same backup carries everything.
    careful = back_up(data, tmp_path / "careful", TODAY, store=store)
    restore(careful.path, tmp_path / "from-careful")
    careful_store = SqliteStore(
        open_database(tmp_path / "from-careful" / "agent.db"),
        files_dir=tmp_path / "from-careful" / "files",
    )
    assert careful_store.get_item(item_id).consultant == "Priya Shah"


class TestTheCommands:
    """`fops backup` and `fops restore` as an operator types them."""

    def env(self, monkeypatch: pytest.MonkeyPatch, data_dir: Path) -> None:
        for name, value in {
            "FOPS_MODE": "dry_run",
            "FOPS_TIMEZONE": "America/New_York",
            "FOPS_ENGAGEMENT_LIST": str(data_dir / "engagements.xlsx"),
            "FOPS_ADMIN_EMAIL": "kevin@icon-technologies.com",
            "FOPS_AGENT_MAILBOX": "jay@icon-technologies.com",
            "FOPS_DATA_DIR": str(data_dir),
        }.items():
            monkeypatch.setenv(name, value)

    def test_backup_then_restore_into_an_empty_folder(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        data = tmp_path / "data"
        data.mkdir()
        store = populated(data)
        item_id = store.list_items()[0].id
        self.env(monkeypatch, data)

        assert main(["backup", "--to", str(tmp_path / "backups")]) == 0
        assert "Backed up" in capsys.readouterr().out
        [archive] = list((tmp_path / "backups").glob("*.zip"))

        # A different data folder, as if this were a new machine.
        restored_dir = tmp_path / "restored"
        self.env(monkeypatch, restored_dir)
        assert main(["restore", str(archive)]) == 0

        restored = SqliteStore(
            open_database(restored_dir / "agent.db"), files_dir=restored_dir / "files"
        )
        assert restored.get_item(item_id).consultant == "Priya Shah"

    def test_restore_over_a_folder_in_use_says_no_and_changes_nothing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        data = tmp_path / "data"
        data.mkdir()
        populated(data)
        self.env(monkeypatch, data)
        assert main(["backup", "--to", str(tmp_path / "backups")]) == 0
        [archive] = list((tmp_path / "backups").glob("*.zip"))
        capsys.readouterr()

        assert main(["restore", str(archive)]) == 1
        assert "not empty" in capsys.readouterr().out
