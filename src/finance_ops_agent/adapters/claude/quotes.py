"""Check the model's quotes against the PDF's own text layer.

Citations can't be combined with structured output, so evidence is part of the
form; when the PDF has a text layer, each quote must actually appear in it, and
a field whose quote does not is trusted one level less.
"""

import io
import re

from pypdf import PdfReader

from finance_ops_agent.domain.reading import Confidence, ReadField, TimesheetReading

_QUOTED_FIELDS = (
    "consultant_name",
    "client_name",
    "end_client_name",
    "period_start",
    "period_end",
    "stated_total_hours_hundredths",
    "approval",
)

_LOWERED = {
    Confidence.HIGH: Confidence.MEDIUM,
    Confidence.MEDIUM: Confidence.LOW,
    Confidence.LOW: Confidence.LOW,
}


def pdf_text(content: bytes) -> str:
    """The PDF's text layer; empty for scans and anything unreadable."""
    try:
        reader = PdfReader(io.BytesIO(content))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception:
        return ""


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).casefold().strip()


def check_quotes(reading: TimesheetReading, text: str) -> TimesheetReading:
    """Lower the confidence of any field whose quote is not in the text layer."""
    haystack = _normalise(text)
    if not haystack:
        return reading  # no text layer (a scan): nothing to check against
    changes: dict[str, ReadField[object]] = {}
    for field_name in _QUOTED_FIELDS:
        field: ReadField[object] = getattr(reading, field_name)
        if field.quote and _normalise(field.quote) not in haystack:
            changes[field_name] = field.model_copy(
                update={"confidence": _LOWERED[field.confidence]}
            )
    if not changes:
        return reading
    return reading.model_copy(update=changes)
