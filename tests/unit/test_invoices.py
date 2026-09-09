import io
from datetime import date

import pytest
from pypdf import PdfReader

from finance_ops_agent.adapters.pdf.writer import TextPdfRenderer
from finance_ops_agent.domain.invoices import build_invoice
from finance_ops_agent.domain.money import Money
from tests.unit.test_emails import worked_example_item


def test_invoice_from_the_worked_example() -> None:
    invoice = build_invoice(worked_example_item(), "ICON-2026-1001", date(2026, 9, 3))
    assert invoice.total == Money(2_184_000)
    assert invoice.bill_rate == Money(14_000)
    assert invoice.due_date == date(2026, 10, 3)  # 30 day terms
    assert invoice.client_legal_name == "Acme Corporation"
    assert invoice.line_description() == (
        "Priya Shah — Senior PeopleSoft Developer — 2026-08-01 to 2026-08-31"
    )


def test_an_item_without_amounts_cannot_be_invoiced() -> None:
    from dataclasses import replace

    item = replace(worked_example_item(), approved_hours=None, invoice_amount=None)
    with pytest.raises(ValueError):
        build_invoice(item, "ICON-2026-1001", date(2026, 9, 3))


def test_the_pdf_has_a_readable_text_layer_with_the_right_numbers() -> None:
    invoice = build_invoice(worked_example_item(), "ICON-2026-1001", date(2026, 9, 3))
    pdf = TextPdfRenderer().invoice_pdf(invoice)
    text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(pdf)).pages)
    assert "INVOICE ICON-2026-1001" in text
    assert "Acme Corporation" in text
    assert "156.00" in text
    assert "$140.00 per hour" in text
    assert "TOTAL DUE: $21,840.00" in text
    # The pay rate never reaches an invoice.
    assert "100.00" not in text
    assert "15,600" not in text


def test_the_pdf_is_deterministic() -> None:
    invoice = build_invoice(worked_example_item(), "ICON-2026-1001", date(2026, 9, 3))
    renderer = TextPdfRenderer()
    assert renderer.invoice_pdf(invoice) == renderer.invoice_pdf(invoice)


def test_a_replacement_invoice_says_so() -> None:
    invoice = build_invoice(
        worked_example_item(), "ICON-2026-1002", date(2026, 9, 5), "ICON-2026-1001"
    )
    pdf = TextPdfRenderer().invoice_pdf(invoice)
    text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(pdf)).pages)
    assert "replaces invoice ICON-2026-1001" in text
