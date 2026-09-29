"""Which engagements to expect timesheets for, once QuickBooks has been asked.

The engagement list used to answer this on its own, from its `Active` column.
The engagements themselves now live in QuickBooks -- a product under a category
per engagement, carrying both rates (docs/decisions.md #36 and #38) -- so
QuickBooks is what says an engagement is live, and marking a product inactive
is how Kevin says one has finished (docs/decisions.md #42).

What QuickBooks cannot say is *when* to expect a timesheet: the billing
schedule, the start date and the first period live only in the workbook. So
this is a join, not a replacement, and an engagement in QuickBooks with no row
behind it is something to tell Kevin about rather than something to guess a
schedule for.

Names are matched here, not compared: `MasTec` in the workbook and `MasTec Inc`
as the category in QuickBooks are the same client, and a client is tried under
every name the list has for it.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field

from finance_ops_agent.domain import checks
from finance_ops_agent.domain.engagements import Client, EngagementWorkbook

# (consultant, client), spelled as the workbook spells them.
Pair = tuple[str, str]


@dataclass(frozen=True)
class LiveEngagements:
    """The answer, split by what can be done about each part."""

    # Expect timesheets for these: live in QuickBooks and schedulable here.
    live: list[Pair] = field(default_factory=list)
    # In QuickBooks, with no row on the Engagements sheet to schedule from.
    # Kevin is told: the agent cannot know when a period ends.
    without_a_row: list[str] = field(default_factory=list)
    # The engagement list still calls these active, and QuickBooks does not
    # have them. No new periods are expected; work already in hand carries on.
    finished: list[Pair] = field(default_factory=list)


def _client_names(client: Client) -> list[str]:
    return [name for name in (client.name, client.quickbooks_customer, client.legal_name) if name]


def _matching_client(workbook: EngagementWorkbook, category: str) -> Client | None:
    """The client this QuickBooks category is, under any of its names."""
    for client in workbook.clients:
        if any(checks.names_match(name, category) for name in _client_names(client)):
            return client
    return None


def live_engagements(
    workbook: EngagementWorkbook,
    from_accounting: Sequence[tuple[str, str]],
) -> LiveEngagements:
    """Join what the accounting system lists to the rows that can schedule it.

    `from_accounting` is (consultant, client) as the accounting system spells
    them. An empty list means the accounting system had nothing to say -- manual
    mode, or a company whose products are not filled in yet -- and the
    engagement list decides on its own, exactly as it did before.
    """
    # In the workbook's own order throughout. Items are created in the order
    # they come back, and their numbers are what Kevin sees in the tracking
    # sheet and the review emails, so they follow his rows rather than the
    # alphabet.
    on_the_list = list(
        dict.fromkeys(
            (engagement.consultant, engagement.client) for engagement in workbook.engagements
        )
    )
    active = [
        pair
        for pair in on_the_list
        if any(
            (engagement.consultant, engagement.client) == pair and engagement.active
            for engagement in workbook.engagements
        )
    ]
    if not from_accounting:
        return LiveEngagements(live=active)

    matched: list[Pair] = []
    without_a_row: list[str] = []
    for consultant, category in from_accounting:
        client = _matching_client(workbook, category)
        pair = _row_for(on_the_list, consultant, client.name if client else category)
        if pair is None:
            described = f"{consultant} at {category}"
            if described not in without_a_row:
                without_a_row.append(described)
            continue
        if pair not in matched:
            matched.append(pair)
    return LiveEngagements(
        live=[pair for pair in on_the_list if pair in matched],
        without_a_row=without_a_row,
        finished=[pair for pair in active if pair not in matched],
    )


def _row_for(on_the_list: list[Pair], consultant: str, client: str) -> Pair | None:
    """The workbook's own spelling of this pair, or None if it has no row.

    Every row is considered, active or not: QuickBooks decides whether an
    engagement is live, and a row left marked inactive by hand must not also
    stop the schedule being read off it.
    """
    for pair in on_the_list:
        if checks.names_match(pair[0], consultant) and checks.names_match(pair[1], client):
            return pair
    return None
