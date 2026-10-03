"""Copy the pictures from docs/how-it-works.md into the overview page.

    uv run python docs/overview/sync_diagrams.py

The overview (`overview.html`) shows the same seven diagrams as
`how-it-works.md`. This puts the current ones in place;
`tests/unit/test_overview_page.py` fails until it has run after a picture
changes. The words around the pictures are written by hand.
"""

import html
import re
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent
HOW = DOCS / "how-it-works.md"
PAGE = DOCS / "overview" / "overview.html"
BLOCK = re.compile(r'<pre class="mermaid" data-diagram="(\d+)">\n(.*?)</pre>', re.S)


def diagrams() -> list[str]:
    return re.findall(r"```mermaid\n(.*?)```", HOW.read_text(), re.S)


def page_diagrams(text: str) -> dict[int, str]:
    return {int(number): html.unescape(body) for number, body in BLOCK.findall(text)}


def main() -> None:
    wanted = diagrams()
    text = PAGE.read_text()

    def replace(match: re.Match[str]) -> str:
        number = int(match.group(1))
        return (
            f'<pre class="mermaid" data-diagram="{number}">\n'
            f"{html.escape(wanted[number - 1], quote=False)}</pre>"
        )

    PAGE.write_text(BLOCK.sub(replace, text))
    print(f"{len(wanted)} diagram(s) copied into {PAGE.relative_to(DOCS.parent)}")


if __name__ == "__main__":
    main()
