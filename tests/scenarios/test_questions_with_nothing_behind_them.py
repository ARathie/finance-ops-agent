"""Questions with no timesheet or invoice behind them (decision 66, gap G7).

A problem in the engagement list is fixed in the list, not by a reply: its
question closes by itself on the first run that finds the row right, and a
reply is answered with what is still to fix. A question about an email with no
usable timesheet cannot take an answer either. And a reply to one such email
only ever closes that email's own questions.
"""

from datetime import date

from finance_ops_agent.domain.reading import ReplyAnswer, ReplyAnswerKind, ReplyReading
from tests.scenarios.conftest import PRIYA, ScenarioEnv, client_row

AUG_START, AUG_END = date(2026, 8, 1), date(2026, 8, 31)
NO_CODE = 'Clients sheet, row 3: "Invoice code" is empty, so invoices cannot be numbered'
BAD_CODE = "Clients sheet, row 4: \"Invoice code\" should be two letters like MT, not 'X1'"


def other_client(row: int, name: str, code: str) -> dict[str, str]:
    """A second client with no engagements, so a fault in its row touches nothing else."""
    return {
        "Client": name,
        "Legal name": name,
        "Billing email": f"ap@{name.lower()}.example",
        "Names on timesheets": name,
        "Email domains": f"{name.lower()}.example",
        "Invoice code": code,
    }


def list_problems(env: ScenarioEnv) -> list[str]:
    return sorted(
        review.message
        for review in env.store.open_reviews()
        if review.code == "LIST_ROW_PROBLEM" and review.item_id is None
    )


def two_list_problems(env: ScenarioEnv) -> str:
    """A run over a list with two bad rows; returns the subject Kevin got."""
    env.workbook.clients.append(client_row(3, **other_client(3, "Beta", "")))
    env.workbook.clients.append(client_row(4, **other_client(4, "Gamma", "X1")))
    env.run()
    assert list_problems(env) == [NO_CODE, BAD_CODE]
    return next(s for s in env.sent_subjects() if "the engagement list" in s)


def emails_to_kevin_about(env: ScenarioEnv, subject: str) -> list[str]:
    return [email.body for email in env.sender.sent_emails() if email.subject == subject]


class TestListProblemsCloseThemselves:
    def test_a_fixed_row_closes_its_question_on_the_next_run(self, env: ScenarioEnv) -> None:
        two_list_problems(env)

        env.workbook.clients[1] = client_row(3, **other_client(3, "Beta", "BE"))
        env.run()

        assert list_problems(env) == [BAD_CODE]  # the other is still wrong, still asked

    def test_the_email_says_there_is_nothing_to_reply(self, env: ScenarioEnv) -> None:
        subject = two_list_problems(env)

        [body] = emails_to_kevin_about(env, subject)
        assert "closes by" in body and "there is nothing to reply" in body
        assert "reply to this\nemail with the answer" not in body

    def test_a_question_raised_another_way_is_left_alone(self, env: ScenarioEnv) -> None:
        """Only the list's own problems close when the list no longer has them:
        a question like "QuickBooks has an engagement the list has no row for"
        is not one of them."""
        env.store.open_review(None, "LIST_ROW_PROBLEM", "QuickBooks has an engagement for X")
        env.run()
        env.run()
        assert "QuickBooks has an engagement for X" in list_problems(env)


class TestAReplyAboutTheList:
    def test_it_changes_nothing_and_says_what_is_still_to_fix(self, env: ScenarioEnv) -> None:
        subject = two_list_problems(env)
        env.workbook.clients[1] = client_row(3, **other_client(3, "Beta", "BE"))  # one fixed
        # Were Claude asked, this would close every question. It must not be:
        # a reply cannot fix a row.
        env.replies["ignore"] = ReplyReading(
            answers=[ReplyAnswer(review_code="LIST_ROW_PROBLEM", kind=ReplyAnswerKind.IGNORE)]
        )
        env.reply_from_kevin(subject, "ignore")
        env.run()

        assert list_problems(env) == [BAD_CODE]
        [note] = emails_to_kevin_about(env, f"Re: {subject}")
        assert "A reply can't change the engagement list" in note
        still, fixed = note.split("Already fixed:")
        assert BAD_CODE in still
        assert NO_CODE in fixed

    def test_an_answer_to_that_note_gets_nothing_more(self, env: ScenarioEnv) -> None:
        subject = two_list_problems(env)
        env.reply_from_kevin(subject, "the code is AC")
        env.run()
        [note] = [
            record for record in env.store.outgoing_records() if record.kind == "list_reply_email"
        ]
        sent = len(env.sender.sent_emails())

        env.add_email(
            "kevin@icon-technologies.com",
            subject=f"Re: {note.payload['subject']}",
            attachment=None,
            body="I am out of the office until Monday.",
            in_reply_to=note.message_id,
        )
        env.run()

        assert len(env.sender.sent_emails()) == sent


class TestAnEmailWithNoTimesheet:
    def no_attachment(self, env: ScenarioEnv) -> str:
        env.add_email(PRIYA, subject="My timesheet", attachment=None)
        env.run()
        return next(s for s in env.sent_subjects() if "no attachment" in s)

    def test_an_answer_is_not_written_down_as_if_it_did_something(self, env: ScenarioEnv) -> None:
        subject = self.no_attachment(env)
        env.replies["use 152 hours"] = ReplyReading(
            answers=[
                ReplyAnswer(
                    review_code="NO_ATTACHMENT",
                    kind=ReplyAnswerKind.HOURS,
                    value="152",
                    quote="use 152 hours",
                )
            ]
        )
        env.reply_from_kevin(subject, "use 152 hours")
        env.run()

        assert "NO_ATTACHMENT" in {review.code for review in env.store.open_reviews()}
        [note] = emails_to_kevin_about(env, f"Re: {subject}")
        assert "There's no timesheet behind this question" in note
        assert all(not env.store.timesheets_for_item(item.id) for item in env.store.list_items())

    def test_ignore_closes_only_this_emails_question(self, env: ScenarioEnv) -> None:
        two_list_problems(env)
        subject = self.no_attachment(env)
        env.replies["ignore"] = ReplyReading(
            answers=[ReplyAnswer(review_code="NO_ATTACHMENT", kind=ReplyAnswerKind.IGNORE)]
        )
        env.reply_from_kevin(subject, "ignore")
        env.run()

        assert "NO_ATTACHMENT" not in {review.code for review in env.store.open_reviews()}
        # Before decision 66 an "ignore" here closed every question without an
        # item, the engagement list's among them.
        assert list_problems(env) == [NO_CODE, BAD_CODE]
