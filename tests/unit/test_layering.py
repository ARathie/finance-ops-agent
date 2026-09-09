"""domain/ and application/ never import an adapter (CLAUDE.md, decision 6).

The rule is what keeps the whole flow testable on fakes with no network, so it
is worth a test rather than a good intention: an import added in a hurry is
exactly how a layer quietly stops being replaceable.
"""

import ast
from pathlib import Path

import pytest

SOURCE = Path(__file__).parent.parent.parent / "src" / "finance_ops_agent"
INNER_LAYERS = ("domain", "application", "ports")


def imported_modules(path: Path) -> set[str]:
    """Every module a file imports, including inside functions."""
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def inner_files() -> list[Path]:
    return sorted(path for layer in INNER_LAYERS for path in (SOURCE / layer).rglob("*.py"))


def test_there_are_files_to_check() -> None:
    assert len(inner_files()) > 10  # a passing test on an empty list proves nothing


@pytest.mark.parametrize("path", inner_files(), ids=lambda path: str(path.name))
def test_no_inner_layer_imports_an_adapter(path: Path) -> None:
    offenders = sorted(
        name
        for name in imported_modules(path)
        if name.startswith("finance_ops_agent.adapters") or name.startswith("finance_ops_agent.cli")
    )
    assert not offenders, f"{path.relative_to(SOURCE)} imports {offenders}"
