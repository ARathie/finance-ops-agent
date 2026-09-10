import base64
import io
from pathlib import Path

import pytest
from PIL import Image

from finance_ops_agent.adapters.claude.attachments import (
    MAX_IMAGE_SIDE,
    to_content_blocks,
)
from finance_ops_agent.ports.reader import CantReadAttachmentError

FIXTURES = Path(__file__).parent.parent / "evals" / "timesheets"


def a_case_with(suffix: str) -> bytes:
    """Any generated case of this format.

    Named cases used to be hard-coded here, which quietly tied these tests to
    whichever cases `build_test_set.py` happened to produce: rebalancing the
    set broke them. What matters is the format, not which case supplies it.
    """
    found = sorted(FIXTURES.glob(f"*/input{suffix}"))
    assert found, f"the test set has no {suffix} case"
    return found[0].read_bytes()


def test_pdf_becomes_a_document_block() -> None:
    content = a_case_with(".pdf")
    [block] = to_content_blocks(content, "timesheet.pdf")
    assert block["type"] == "document"
    source = block["source"]
    assert isinstance(source, dict)
    assert source["media_type"] == "application/pdf"
    assert base64.standard_b64decode(str(source["data"])) == content


def test_oversized_pdf_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("finance_ops_agent.adapters.claude.attachments.MAX_PDF_BYTES", 100)
    content = a_case_with(".pdf")
    with pytest.raises(CantReadAttachmentError):
        to_content_blocks(content, "huge.pdf")


def test_broken_pdf_is_refused() -> None:
    with pytest.raises(CantReadAttachmentError):
        to_content_blocks(b"not a pdf at all", "broken.pdf")


def test_image_becomes_an_image_block() -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (200, 100), "white").save(buffer, format="PNG")
    [block] = to_content_blocks(buffer.getvalue(), "scan.png")
    assert block["type"] == "image"


def test_very_large_images_are_downscaled() -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (MAX_IMAGE_SIDE * 2, 500), "white").save(buffer, format="PNG")
    [block] = to_content_blocks(buffer.getvalue(), "huge.png")
    source = block["source"]
    assert isinstance(source, dict)
    downscaled = Image.open(io.BytesIO(base64.standard_b64decode(str(source["data"]))))
    assert max(downscaled.size) <= MAX_IMAGE_SIDE


def test_csv_becomes_text() -> None:
    [block] = to_content_blocks(b"Date,Hours\n2026-08-03,8.00\n", "hours.csv")
    assert block["type"] == "text"
    assert "2026-08-03\t8.00" in str(block["text"])


def test_csv_rows_are_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("finance_ops_agent.adapters.claude.attachments.MAX_SHEET_ROWS", 3)
    content = "\n".join(f"row{i}" for i in range(10)).encode()
    [block] = to_content_blocks(content, "long.csv")
    text = str(block["text"])
    assert "row2" in text and "row9" not in text and "limit reached" in text


CONSULTANTS_IN_THE_SET = [
    "Priya Shah",
    "Dana Cruz",
    "Marcus Webb",
    "Elena Petrov",
    "Tom Nakamura",
    "Aisha Bell",
]


def test_xlsx_becomes_text() -> None:
    content = a_case_with(".xlsx")
    [block] = to_content_blocks(content, "timesheet.xlsx")
    text = str(block["text"])
    assert any(name in text for name in CONSULTANTS_IN_THE_SET), text[:200]


def test_docx_becomes_text() -> None:
    content = a_case_with(".docx")
    [block] = to_content_blocks(content, "timesheet.docx")
    text = str(block["text"])
    assert any(name in text for name in CONSULTANTS_IN_THE_SET), text[:200]


@pytest.mark.parametrize("filename", ["archive.zip", "notes.txt", "no-extension"])
def test_anything_else_is_refused(filename: str) -> None:
    with pytest.raises(CantReadAttachmentError):
        to_content_blocks(b"whatever", filename)
