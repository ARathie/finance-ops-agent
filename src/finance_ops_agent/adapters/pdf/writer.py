"""A small PDF writer: one page of Helvetica text with a real text layer.

No system libraries, no third-party renderer, fully deterministic - which is
exactly what snapshot tests and an unattended Mac want (docs/decisions.md #16).
pypdf can extract every line back out, which the quote checker relies on too.
"""

import io

from finance_ops_agent.domain.emails import dollars, format_period
from finance_ops_agent.domain.invoices import Invoice

PAGE_WIDTH, PAGE_HEIGHT = 612, 792
MARGIN, LEADING = 72, 16


def _escape(text: str) -> str:
    return (
        text.replace("\\", r"\\")
        .replace("(", r"\(")
        .replace(")", r"\)")
        .encode("latin-1", errors="replace")
        .decode("latin-1")
    )


def text_pdf(lines: list[str]) -> bytes:
    content_lines = [f"BT /F1 12 Tf {MARGIN} {PAGE_HEIGHT - MARGIN} Td {LEADING} TL"]
    for line in lines:
        content_lines.append(f"({_escape(line)}) Tj T*")
    content_lines.append("ET")
    stream = "\n".join(content_lines).encode("latin-1")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] /Contents 4 0 R"
        b" /Resources << /Font << /F1 5 0 R >> >> >>" % (PAGE_WIDTH, PAGE_HEIGHT),
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % number)
        out.write(body)
        out.write(b"\nendobj\n")
    xref_at = out.tell()
    out.write(b"xref\n0 %d\n" % (len(objects) + 1))
    out.write(b"0000000000 65535 f \n")
    for offset in offsets:
        out.write(b"%010d 00000 n \n" % offset)
    out.write(
        b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
        % (len(objects) + 1, xref_at)
    )
    return out.getvalue()


class TextPdfRenderer:
    """The PdfRenderer port; the fake is the same as the real thing."""

    def invoice_pdf(self, invoice: Invoice) -> bytes:
        lines = [
            "ICON TECHNOLOGIES",
            "",
            f"INVOICE {invoice.number}",
            "",
            f"Bill to: {invoice.client_legal_name}",
            f"Invoice date: {invoice.issue_date}",
            f"Due date: {invoice.due_date}",
        ]
        if invoice.replaces_number:
            lines.append(
                f"This invoice replaces invoice {invoice.replaces_number},"
                " which has been cancelled."
            )
        lines += [
            "",
            "Description",
            f"  {invoice.line_description()}",
            "",
            f"Quantity (hours): {invoice.approved_hours}",
            f"Rate: {dollars(invoice.bill_rate)} per hour",
            f"Period: {format_period(invoice.period)}",
            "",
            f"TOTAL DUE: {dollars(invoice.total)}",
        ]
        return text_pdf(lines)
