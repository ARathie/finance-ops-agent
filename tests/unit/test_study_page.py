"""The study page shows the same pictures as docs/how-it-works.md.

The page is published as "Anatomy of a Billing Agent" and is how the project
is learned and explained. A picture that changed in one place and not the
other would teach the agent as it used to be. If this fails, run
`uv run python docs/study/sync_diagrams.py`, update the words around the
picture on the page, and republish it.
"""

import importlib.util
from pathlib import Path
from types import ModuleType

PAGE = Path(__file__).resolve().parents[2] / "docs" / "study"


def _sync_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("sync_diagrams", PAGE / "sync_diagrams.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_picture_is_on_the_page_as_it_is_in_the_docs() -> None:
    sync = _sync_module()
    wanted = sync.diagrams()
    shown = sync.page_diagrams((PAGE / "anatomy.html").read_text())
    assert sorted(shown) == list(range(1, len(wanted) + 1)), "a picture is missing from the page"
    for number, diagram in enumerate(wanted, start=1):
        assert shown[number] == diagram, (
            f"picture {number} on the study page differs from docs/how-it-works.md;"
            " run `uv run python docs/study/sync_diagrams.py`"
        )
