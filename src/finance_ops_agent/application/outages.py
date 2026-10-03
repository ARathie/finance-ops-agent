"""When the mailbox or Claude cannot be reached (decision 58).

Neither stops the agent for good: an email that could not be fetched is
fetched on the next run, and one Claude could not read is read on the next run,
because nothing is marked done until it is. What was missing was anybody being
told. A blip that clears by the next run is not worth Kevin's time; one that
lasts is, and so is a refused password or key, which never clears by itself.

So each outage is remembered from the first failure. Kevin is emailed once
when it has lasted an hour -- or at once when it will not clear by itself --
and once more when it is over. The review closes itself.

If the mailbox is down the email to Kevin may not get out either, since it
goes through the same provider: it waits in the outgoing table and is sent as
soon as sending works. The heartbeat (`fops serve`) is what covers an agent
that cannot say anything at all.
"""

from datetime import datetime, timedelta

from finance_ops_agent import logs
from finance_ops_agent.application import outgoing as outgoing_steps
from finance_ops_agent.application.context import RunDeps, RunReport
from finance_ops_agent.domain import emails
from finance_ops_agent.domain.review import ReviewCode
from finance_ops_agent.ports.inbox import MailboxFailed

GRACE = timedelta(hours=1)

MAILBOX = "mailbox"
CLAUDE = "claude"

_WHAT = {
    MAILBOX: (
        ReviewCode.MAILBOX_PROBLEM,
        "the mailbox",
        "New emails are not being read. Nothing is lost: they are read as soon as the"
        " mailbox answers.",
        [
            "Check that the mailbox password in .env is still right, and that the"
            " mailbox at Rackspace works when you sign in to it yourself.",
            "Run `fops doctor` on the machine the agent runs on: its mailbox line says"
            " what is wrong.",
        ],
    ),
    CLAUDE: (
        ReviewCode.CLAUDE_UNAVAILABLE,
        "Claude",
        "Timesheets and your replies are waiting to be read. Nothing is lost: they are"
        " read as soon as Claude answers.",
        [
            "Check that ANTHROPIC_API_KEY in .env is still right and the account has credit.",
            "If the key is fine, Anthropic may be having trouble; it usually clears by itself.",
        ],
    ),
}


def _since_key(which: str) -> str:
    return f"outage:{which}:since"


def _told_key(which: str) -> str:
    return f"outage:{which}:told"


def failed(deps: RunDeps, report: RunReport, which: str, error: Exception, lasting: bool) -> None:
    """Remember the outage, and tell Kevin once it is worth telling him."""
    now = deps.clock.now()
    since_text = deps.store.get_state(_since_key(which))
    if not since_text:
        since_text = now.isoformat()
        deps.store.set_state(_since_key(which), since_text)
    since = datetime.fromisoformat(since_text)
    said = str(error)[:300]
    logs.log("outage", which=which, since=since_text, lasting=lasting, said=said)
    report.note(f"could not reach {_WHAT[which][1]}: {said}")
    if deps.store.get_state(_told_key(which)):
        return  # told once per outage, not once a run
    if not lasting and now - since < GRACE:
        return  # a blip, until it has lasted
    code, name, effect, steps = _WHAT[which]
    message = (
        f"I haven't been able to reach {name} since {since:%Y-%m-%d %H:%M}. {effect}"
        f" It said: {said}"
    )
    if deps.store.open_review(None, code.value, message):
        report.reviews_opened += 1
    deps.store.set_state(_told_key(which), message)
    email = emails.needs_review(
        deps.settings.admin_email,
        name,
        [message],
        what_you_can_do=[
            *steps,
            "Nothing else is needed: I try again on every run and tell you when it works again.",
        ],
    )
    outgoing_steps.enqueue_email(
        deps, "review_email", f"outage:{which}:{since_text}", None, email, extra={"outage": which}
    )


def working(deps: RunDeps, report: RunReport, which: str) -> None:
    """It answered. Close the outage, and tell Kevin if he was told it began."""
    since_text = deps.store.get_state(_since_key(which))
    if not since_text:
        return
    told = deps.store.get_state(_told_key(which)) or ""
    deps.store.set_state(_since_key(which), "")
    deps.store.set_state(_told_key(which), "")
    code, name, _effect, _steps = _WHAT[which]
    for review in deps.store.open_reviews():
        if review.item_id is None and review.code == code.value and review.message == told:
            deps.store.answer_review(review.id, {"kind": "resolved"}, "answered")
    logs.log("outage over", which=which, since=since_text)
    if not told:
        return  # a blip Kevin never heard about stays that way
    report.note(f"{name} is answering again")
    email = emails.OutgoingEmail(
        to=(deps.settings.admin_email,),
        subject=f"Working again: {name}",
        body=(
            f"I can reach {name} again. Anything that was waiting is being handled now;"
            " there is nothing for you to do."
        ),
    )
    outgoing_steps.enqueue_email(
        deps, "details_email", f"outage-over:{which}:{since_text}", None, email
    )


def close_review(deps: RunDeps, which: str) -> None:
    """Kevin said "ignore": close the question. Still remembered as an
    outage, so he is not told again until it has ended and begun anew."""
    told = deps.store.get_state(_told_key(which)) or ""
    code = _WHAT[which][0]
    for review in deps.store.open_reviews():
        if review.item_id is None and review.code == code.value and review.message == told:
            deps.store.answer_review(review.id, {"kind": "ignore"}, "ignored")


def file_in(deps: RunDeps, message_id: str, folder: str) -> None:
    """File an email in one of the agent's folders, as a courtesy.

    The database is the record; the folder is only for a person looking at
    the mailbox. A mailbox that will not answer is no reason to leave the
    email unhandled -- that would handle it twice -- so a failed move is
    logged and the run carries on."""
    try:
        deps.inbox.move(message_id, folder)
    except MailboxFailed as error:
        logs.log("could not file an email", folder=folder, said=str(error)[:200])
