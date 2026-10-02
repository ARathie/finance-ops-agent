"""The form Claude fills in when it reads a timesheet.

Claude reads; code decides (docs/decisions.md #14). Every field carries the exact
words on the page it relied on and how sure the model is. The model never sums
hours, never sees a rate, and never picks recipients; `summed_daily_hundredths`
below is the code doing its own arithmetic. All fields are required: a reading is
always the whole form, never a fragment (a failed read becomes
CANT_READ_ATTACHMENT, not a partial reading).
"""

from datetime import date
from enum import StrEnum
from typing import Annotated, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ApprovalKind(StrEnum):
    """What shows the client approved the hours (docs/timesheet-checks.md)."""

    APPROVED_STATUS = "approved_status"
    APPROVER_NAME_DATE = "approver_name_date"
    SIGNATURE = "signature"
    FORWARDED_EMAIL = "forwarded_email"
    NONE = "none"


class ReadField(BaseModel, Generic[T]):
    """One answer on the form: the value, the words on the page, and how sure."""

    model_config = ConfigDict(frozen=True)

    value: T | None = None
    quote: str | None = None
    confidence: Confidence = Confidence.LOW


class DailyEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    day: date
    hours_hundredths: Annotated[int, Field(ge=0)]


class RowEntry(BaseModel):
    """One dated row of hours covering a stretch of days, usually a week.

    Icon's timesheets are weekly, not daily: a row carries a week's hours
    against one printed date (docs/open-questions.md). `label` keeps that date
    exactly as printed, because a document may head the column "week ending"
    while printing the week's first day; `first_day` and `last_day` are the
    span the reader believes the row covers. Nothing here is added up or
    apportioned by the model — code does that against the billing period.
    """

    model_config = ConfigDict(frozen=True)

    label: str | None = None
    first_day: date | None = None
    last_day: date | None = None
    hours_hundredths: Annotated[int, Field(ge=0)]

    def straddles(self, period_start: date, period_end: date) -> bool:
        """True when part of this row falls outside the billing period.

        Then the row's hours are not all billable in this period, and how many
        are is not something the model may guess.
        """
        if self.first_day is None or self.last_day is None:
            return False
        return self.first_day < period_start or self.last_day > period_end


class Approval(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: ApprovalKind = ApprovalKind.NONE
    approver: str | None = None
    approval_date: date | None = None


class TimesheetReading(BaseModel):
    model_config = ConfigDict(frozen=True)

    consultant_name: ReadField[str]
    client_name: ReadField[str]
    end_client_name: ReadField[str]
    period_start: ReadField[date]
    period_end: ReadField[date]
    # The month this document says it bills, as that month's first day. A
    # weekly timesheet's rows span two months and never say which is being
    # billed; a vendor invoice does (decision 26). Kept as a plain date so the
    # form asks nothing of the model it is not already asked elsewhere.
    stated_month_start: ReadField[date] = ReadField[date]()
    # Hours a note assigns to the billed month for a week that straddles it.
    noted_in_month_hundredths: ReadField[int] = ReadField[int]()
    daily_entries: ReadField[list[DailyEntry]]
    row_entries: ReadField[list[RowEntry]] = ReadField[list[RowEntry]](value=[])
    stated_total_hours_hundredths: ReadField[int]
    approval: ReadField[Approval]
    unusual_items: list[str] = Field(default_factory=list)

    def summed_daily_hundredths(self) -> int | None:
        """Code sums the daily hours itself; None when the timesheet showed no daily hours."""
        entries = self.daily_entries.value
        if not entries:
            return None
        return sum(entry.hours_hundredths for entry in entries)

    def summed_row_hundredths(self) -> int | None:
        """Code sums the weekly rows itself; None when the timesheet showed none."""
        rows = self.row_entries.value
        if not rows:
            return None
        return sum(row.hours_hundredths for row in rows)

    def rows_outside_period(self) -> list[RowEntry]:
        """Rows running past either end of the period the timesheet says it covers."""
        start, end = self.period_start.value, self.period_end.value
        if start is None or end is None:
            return []
        return [row for row in self.row_entries.value or [] if row.straddles(start, end)]


class ClassifiedKind(StrEnum):
    """What the model says an ambiguous email is (docs/integrations/claude-extraction.md).

    Code applies its simple rules first (sender is Kevin, sender unknown); the
    model only sees the ambiguous ones, and its answer still goes through the
    checks like everything else.
    """

    TIMESHEET = "timesheet"
    CORRECTED_TIMESHEET = "corrected_timesheet"
    APPROVAL_FROM_CLIENT = "approval_from_client"
    REPLY_FROM_KEVIN = "reply_from_kevin"
    CLIENT_REPLY = "client_reply"
    OTHER = "other"


class EmailClassification(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: ClassifiedKind
    reason: str  # one line


class ReplyAnswerKind(StrEnum):
    """What one part of Kevin's reply asks for.

    A reply is free text, and one reply can ask for several things ("use
    083126MT-MK-revised, and send it to me before it goes out"). The model
    names each of them as one of these; code checks each one and either does
    it or tells Kevin why not. Anything outside this list is `unclear`, never
    a guess (docs/decisions.md #54).
    """

    CONSULTANT_NAME = "consultant_name"
    CLIENT_NAME = "client_name"
    PERIOD_START = "period_start"
    PERIOD_END = "period_end"
    HOURS = "hours"
    APPROVAL_NOTE = "approval_note"
    IGNORE = "ignore"
    USE_NEW_ONE = "use_new_one"
    APPROVE = "approve"  # send the invoice he was shown, unchanged
    CANCEL = "cancel"  # stop it; the invoice is voided
    INVOICE_NUMBER = "invoice_number"  # make the invoice under this number instead
    TRY_AGAIN = "try_again"  # he fixed something outside the agent; go again
    SHOW_ME_FIRST = "show_me_first"  # he wants to approve this one before it goes out
    UNCLEAR = "unclear"


class ReplyAnswer(BaseModel):
    model_config = ConfigDict(frozen=True)

    review_code: str  # which review reason this answers ("APPROVAL" for an approval request)
    kind: ReplyAnswerKind
    # Dates as YYYY-MM-DD, hours as written ("152"), names as written, and an
    # invoice number spelled out in full as Kevin wants it to read.
    value: str | None = None
    quote: str | None = None  # Kevin's words


class ReplyReading(BaseModel):
    """The model's reading of Kevin's reply. Code applies it; `unclear` means ask again."""

    model_config = ConfigDict(frozen=True)

    answers: list[ReplyAnswer]
    # In plain words, for the email back to Kevin: what he asked for, and
    # what is still missing if anything is. Never instructions to the code.
    understood: str = ""
    still_unclear: str = ""


class ReplyContext(BaseModel):
    """What the reply is about, so a reply in Kevin's own words can be read.

    Names, the period and the invoice number only: never rates, never
    addresses (the same rule as ReadingHints).
    """

    model_config = ConfigDict(frozen=True)

    asked: str  # "approval" or "review"
    consultant: str = ""
    client: str = ""
    period: str = ""
    invoice_number: str = ""
    # The replies the agent offered Kevin as ways out, in letter order (A, B,
    # C), when it looked into the problem first (decision 56).
    offered: list[str] = Field(default_factory=list)


class ReadingHints(BaseModel):
    """What the reader may see besides the attachment: the email text, and the
    consultant and client names from the engagement list for spelling only.
    Never rates, never email addresses."""

    model_config = ConfigDict(frozen=True)

    email_subject: str = ""
    email_text: str = ""
    consultant_names: list[str] = Field(default_factory=list)
    client_names: list[str] = Field(default_factory=list)
