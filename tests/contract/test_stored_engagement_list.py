"""The engagement list in the agent's own store rather than in a file.

What is stored is the workbook exactly as it was read, so nothing downstream
can tell the difference: the same parsing, the same checks, the same problems
naming the same sheet and row (decision 40).
"""

from pathlib import Path

import pytest

from finance_ops_agent.adapters.excel.engagement_list import CsvEngagementList
from finance_ops_agent.adapters.sqlite.store import SqliteStore, open_database
from finance_ops_agent.adapters.stored.engagement_list import (
    NotImported,
    StoredEngagementList,
    from_json,
    imported,
    put,
    to_json,
)
from finance_ops_agent.domain.engagements import parse_workbook

FIXTURES = Path(__file__).parent.parent / "fixtures" / "fake_run" / "engagements"


def a_store(tmp_path: Path) -> SqliteStore:
    return SqliteStore(open_database(tmp_path / "agent.db"), files_dir=tmp_path / "files")


def test_a_workbook_survives_the_round_trip() -> None:
    original = CsvEngagementList(FIXTURES).load()

    again = from_json(to_json(original))

    assert again == original


def test_it_parses_to_the_same_thing(tmp_path: Path) -> None:
    """The point of storing the raw cells: every check downstream is unchanged."""
    original = CsvEngagementList(FIXTURES).load()
    store = a_store(tmp_path)
    put(store, original)

    from_file = parse_workbook(original)
    from_store = parse_workbook(StoredEngagementList(store).load())

    assert from_store == from_file
    assert from_store.problems == []


def test_row_numbers_survive(tmp_path: Path) -> None:
    """A problem has to name the row Kevin sees, wherever the list is kept."""
    store = a_store(tmp_path)
    put(store, CsvEngagementList(FIXTURES).load())

    loaded = StoredEngagementList(store).load()

    assert [row.row_number for row in loaded.clients] == [2]
    assert [row.row_number for row in loaded.engagements] == [2]


def test_nothing_imported_says_what_to_run(tmp_path: Path) -> None:
    store = a_store(tmp_path)

    assert not imported(store)
    with pytest.raises(NotImported, match="fops engagements import"):
        StoredEngagementList(store).load()


def test_importing_again_replaces_it(tmp_path: Path) -> None:
    store = a_store(tmp_path)
    original = CsvEngagementList(FIXTURES).load()
    put(store, original)

    trimmed = type(original)(clients=original.clients, consultants=[], vendors=[], engagements=[])
    put(store, trimmed)

    assert StoredEngagementList(store).load().consultants == []
