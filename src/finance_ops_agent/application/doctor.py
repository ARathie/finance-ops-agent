"""`fops doctor`: check every credential and setting without touching a client.

The important check is the scoping one: the application access policy must
make a second mailbox unreadable. A doctor run that cannot prove that fails,
because an app that can read all of Icon's mail is not what was asked for.
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from finance_ops_agent.adapters.microsoft365.client import GraphClient, MailboxProblem


class CheckResult(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"


@dataclass(frozen=True)
class Check:
    name: str
    result: CheckResult
    detail: str

    def line(self) -> str:
        mark = {CheckResult.PASS: "ok  ", CheckResult.FAIL: "FAIL", CheckResult.SKIP: "--  "}
        return f"{mark[self.result]} {self.name}: {self.detail}"


def _run(name: str, check: Callable[[], str]) -> Check:
    try:
        return Check(name, CheckResult.PASS, check())
    except Exception as error:  # a doctor reports, it never crashes
        return Check(name, CheckResult.FAIL, str(error)[:300])


def check_token(client: GraphClient) -> Check:
    def run() -> str:
        client.get(client.user_url("?$select=mail,displayName"))
        return f"signed in and found {client.mailbox}"

    return _run("token", run)


def check_can_read_the_agent_mailbox(client: GraphClient) -> Check:
    def run() -> str:
        page = client.get(client.user_url("/mailFolders/inbox/messages?$top=1&$select=id,subject"))
        count = len(page.get("value", []))
        return f"read the inbox ({count} recent message(s) visible)"

    return _run("read the agent mailbox", run)


def check_cannot_read_another_mailbox(client: GraphClient, other_mailbox: str) -> Check:
    """The application access policy must deny this. A success is a failure."""
    name = "cannot read anyone else's mailbox"
    if not other_mailbox:
        return Check(
            name,
            CheckResult.FAIL,
            "no other mailbox to test with; set FOPS_ADMIN_EMAIL so I can prove"
            " the app is restricted to the agent mailbox",
        )
    try:
        client.get(f"/users/{other_mailbox}/mailFolders/inbox/messages?$top=1&$select=id")
    except MailboxProblem as error:
        text = str(error)
        if "403" in text or "ErrorAccessDenied" in text:
            return Check(name, CheckResult.PASS, f"{other_mailbox} is denied, as it should be")
        return Check(
            name,
            CheckResult.FAIL,
            f"reading {other_mailbox} failed, but not with a clear denial: {text[:200]}",
        )
    return Check(
        name,
        CheckResult.FAIL,
        f"the app could read {other_mailbox}. Apply the application access policy in"
        " docs/integrations/microsoft-365-email.md before running against real mail.",
    )


def check_engagement_list(load: Callable[[], tuple[int, list[str]]]) -> Check:
    def run() -> str:
        rows, problems = load()
        if problems:
            raise MailboxProblem(
                f"{rows} engagement row(s), but {len(problems)} problem(s): {problems[0]}"
            )
        return f"{rows} engagement row(s), no problems"

    return _run("engagement list", run)


def check_database(describe: Callable[[], str]) -> Check:
    return _run("database", describe)
