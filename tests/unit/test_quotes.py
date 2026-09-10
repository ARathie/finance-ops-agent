from datetime import date
from pathlib import Path

from finance_ops_agent.adapters.claude.quotes import check_quotes, pdf_text
from finance_ops_agent.domain.reading import Confidence
from tests.scenarios.conftest import reading

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


AUG = (date(2026, 8, 1), date(2026, 8, 31))


CONSULTANTS_IN_THE_SET = [
    "Priya Shah",
    "Dana Cruz",
    "Marcus Webb",
    "Elena Petrov",
    "Tom Nakamura",
    "Aisha Bell",
]


def test_pdf_text_extracts_the_text_layer() -> None:
    content = a_case_with(".pdf")
    text = pdf_text(content)
    assert any(name in text for name in CONSULTANTS_IN_THE_SET), text[:200]


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
