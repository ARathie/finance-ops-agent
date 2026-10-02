"""What Kevin has already told the agent about an item, from his answered reviews.

One reply can carry several requests ("use 083126MT-MK-revised and send it to
me first"), so an answered review keeps the whole list under `actions`. Older
answers carry one `kind` and `value` and nothing else; both shapes are read
here, so nothing else has to know there are two (docs/decisions.md #54).
"""

from finance_ops_agent.application.context import RunDeps


def actions_in(answer: dict[str, object] | None) -> list[dict[str, object]]:
    """Every request one answered review carries, in the order Kevin made them."""
    if not answer:
        return []
    listed = answer.get("actions")
    if isinstance(listed, list):
        return [action for action in listed if isinstance(action, dict)]
    return [answer]


def answered_actions(deps: RunDeps, item_id: int) -> list[dict[str, object]]:
    """Everything Kevin has asked for on this item, oldest first."""
    actions: list[dict[str, object]] = []
    for review in deps.store.reviews_for_item(item_id):
        if review.status != "answered":
            continue
        actions.extend(actions_in(deps.store.review_answer(review.id)))
    return actions


def latest_value(deps: RunDeps, item_id: int, kind: str) -> str | None:
    """Kevin's latest answer of one kind for this item; the latest one wins."""
    found: str | None = None
    for action in answered_actions(deps, item_id):
        value = action.get("value")
        if action.get("kind") == kind and isinstance(value, str) and value.strip():
            found = value.strip()
    return found


def chosen_invoice_number(deps: RunDeps, item_id: int) -> str | None:
    """The number Kevin asked for this item's invoice to have, if he did."""
    return latest_value(deps, item_id, "invoice_number")


def wants_to_see_it_first(deps: RunDeps, item_id: int) -> bool:
    """Kevin asked to approve this item's invoice himself before it goes out."""
    return any(action.get("kind") == "show_me_first" for action in answered_actions(deps, item_id))
