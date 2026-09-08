from datetime import date
from pathlib import Path

from finance_ops_agent.adapters.claude.quotes import check_quotes, pdf_text
from finance_ops_agent.domain.reading import Confidence
from tests.scenarios.conftest import reading

FIXTURES = Path(__file__).parent.parent / "evals" / "timesheets"
AUG = (date(2026, 8, 1), date(2026, 8, 31))


def test_pdf_text_extracts_the_text_layer() -> None:
    content = (FIXTURES / "30-pdf-unapproved" / "input.pdf").read_bytes()
    text = pdf_text(content)
    assert "Priya Shah" in text


def test_pdf_text_is_empty_for_unreadable_content() -> None:
    assert pdf_text(b"not a pdf") == ""


def test_a_quote_found_in_the_text_keeps_its_confidence() -> None:
    checked = check_quotes(
        reading(*AUG), "Consultant:  PRIYA   SHAH\nClient: Acme Corp\n2026-08-01 2026-08-31"
    )
    assert checked.consultant_name.confidence is Confidence.HIGH


def test_a_quote_not_in_the_text_is_trusted_one_level_less() -> None:
    checked = check_quotes(reading(*AUG), "Some completely different page text")
    assert checked.consultant_name.confidence is Confidence.MEDIUM
    assert checked.client_name.confidence is Confidence.MEDIUM


def test_no_text_layer_changes_nothing() -> None:
    checked = check_quotes(reading(*AUG), "")
    assert checked == reading(*AUG)


def test_lowering_stops_at_low() -> None:
    low = reading(*AUG, confidence=Confidence.LOW)
    checked = check_quotes(low, "different text entirely")
    assert checked.consultant_name.confidence is Confidence.LOW
