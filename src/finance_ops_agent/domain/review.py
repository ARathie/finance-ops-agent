"""The reasons the agent asks Kevin for a review.

The codes and the plain-language messages are defined in docs/timesheet-checks.md.
Do not add codes here; add them there first (see CLAUDE.md). `NOT_SURE` keeps its
`<field>` placeholder; the email that uses it fills the field name in.
"""

from enum import StrEnum


class ReviewCode(StrEnum):
    UNKNOWN_SENDER = "UNKNOWN_SENDER"
    NO_ATTACHMENT = "NO_ATTACHMENT"
    CANT_READ_ATTACHMENT = "CANT_READ_ATTACHMENT"
    CONSULTANT_UNKNOWN = "CONSULTANT_UNKNOWN"
    ENGAGEMENT_UNCLEAR = "ENGAGEMENT_UNCLEAR"
    PERIOD_UNCLEAR = "PERIOD_UNCLEAR"
    PERIOD_MISMATCH = "PERIOD_MISMATCH"
    HOURS_MISSING = "HOURS_MISSING"
    HOURS_DONT_ADD_UP = "HOURS_DONT_ADD_UP"
    PART_WEEK_UNCLEAR = "PART_WEEK_UNCLEAR"
    HOURS_UNUSUAL = "HOURS_UNUSUAL"
    NO_APPROVAL = "NO_APPROVAL"
    NOT_SURE = "NOT_SURE"
    RATE_MISSING = "RATE_MISSING"
    NO_BILLING_CONTACT = "NO_BILLING_CONTACT"
    LIST_ROW_PROBLEM = "LIST_ROW_PROBLEM"
    CORRECTION = "CORRECTION"
    SEND_FAILED = "SEND_FAILED"
    SEND_UNCERTAIN = "SEND_UNCERTAIN"
    QUICKBOOKS_FAILED = "QUICKBOOKS_FAILED"
    MAILBOX_PROBLEM = "MAILBOX_PROBLEM"
    QUICKBOOKS_RECONNECT = "QUICKBOOKS_RECONNECT"


REVIEW_MESSAGES: dict[ReviewCode, str] = {
    ReviewCode.UNKNOWN_SENDER: "This came from an address I don't recognise.",
    ReviewCode.NO_ATTACHMENT: (
        "This looks like a timesheet email but has no attachment I can use."
    ),
    ReviewCode.CANT_READ_ATTACHMENT: "I couldn't read the attachment.",
    ReviewCode.CONSULTANT_UNKNOWN: "I can't tell which consultant this timesheet is for.",
    ReviewCode.ENGAGEMENT_UNCLEAR: (
        "I can't tell which client this is for, or there is no active engagement for these dates."
    ),
    ReviewCode.PERIOD_UNCLEAR: "I can't tell which dates this covers.",
    ReviewCode.PERIOD_MISMATCH: (
        "The dates don't line up with the billing schedule for this engagement."
    ),
    ReviewCode.HOURS_MISSING: "I can't find the hours on this timesheet.",
    ReviewCode.HOURS_DONT_ADD_UP: "The daily hours don't add up to the total.",
    ReviewCode.PART_WEEK_UNCLEAR: (
        "A week on this timesheet runs past the end of the period I'm billing,"
        " and nothing on it says how many of that week's hours belong to this period."
    ),
    ReviewCode.HOURS_UNUSUAL: "The hours look unusually high, or are zero.",
    ReviewCode.NO_APPROVAL: "I can't see that the client approved these hours.",
    ReviewCode.NOT_SURE: "I read this timesheet but I'm not confident about <field>.",
    ReviewCode.RATE_MISSING: "The engagement list has no rate for these dates.",
    ReviewCode.NO_BILLING_CONTACT: "The engagement list has no billing email for this client.",
    ReviewCode.LIST_ROW_PROBLEM: (
        "A row in the engagement list is incomplete or contradicts another row."
    ),
    ReviewCode.CORRECTION: (
        "This looks like a corrected version of a timesheet I already handled."
    ),
    ReviewCode.SEND_FAILED: (
        "I couldn't send the billing email. I'll keep trying; please check the mailbox."
    ),
    ReviewCode.SEND_UNCERTAIN: (
        "I sent an email but couldn't confirm it left the server. You're on CC:"
        ' reply "received" if you got it, or "resend".'
    ),
    ReviewCode.QUICKBOOKS_FAILED: (
        "I couldn't create the invoice in QuickBooks. I'll keep trying;"
        " please check the connection."
    ),
    ReviewCode.MAILBOX_PROBLEM: "I can't read the mailbox.",
    ReviewCode.QUICKBOOKS_RECONNECT: "QuickBooks needs to be reconnected.",
}
