"""Who an email is from, and so what it is.

Decided by code from the sender's address and the engagement list, never by
reading the email: its content is untrusted (CLAUDE.md rule 7).
"""

from finance_ops_agent.application.context import RunDeps
from finance_ops_agent.domain.engagements import EngagementWorkbook
from finance_ops_agent.domain.messages import MessageKind


def decide_kind(deps: RunDeps, workbook: EngagementWorkbook, from_address: str) -> MessageKind:
    """What an email is, by who sent it: a timesheet, Kevin's reply, a client's
    reply, or something from an address the agent does not know."""
    sender = from_address.strip().casefold()
    if not sender:
        return MessageKind.UNKNOWN_SENDER
    if sender == deps.settings.admin_email.casefold():
        return MessageKind.KEVIN_REPLY
    for consultant in workbook.consultants:
        if sender in (address.casefold() for address in consultant.emails):
            return MessageKind.TIMESHEET
    for vendor in workbook.vendors:
        if sender in (address.casefold() for address in vendor.contact_emails):
            return MessageKind.TIMESHEET
    # Someone forwarding a timesheet on a consultant's behalf (decision 25).
    # The sender says nothing about whose timesheet it is, so match_consultant
    # falls through to the name on the document, which is the point.
    if sender in (address.casefold() for address in deps.settings.timesheet_forwarders):
        return MessageKind.TIMESHEET
    domain = sender.rsplit("@", 1)[-1]
    for client in workbook.clients:
        addresses = [address.casefold() for address in client.billing_emails + client.cc_emails]
        if sender in addresses or domain in (d.casefold() for d in client.email_domains):
            return MessageKind.CLIENT_REPLY
    return MessageKind.UNKNOWN_SENDER
