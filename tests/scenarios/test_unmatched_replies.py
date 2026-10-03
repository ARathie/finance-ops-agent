"""Kevin writes, and nothing the agent sent is what it answers (decision 63).

Gap G4 in docs/pathways.md. The agent still does nothing with it -- guessing
what an email answers is how an instruction lands on the wrong invoice -- but
Kevin now hears so, once, in the same thread.
"""

from finance_ops_agent.domain.reading import ReplyAnswer, ReplyAnswerKind, ReplyReading
from finance_ops_agent.domain.statuses import ItemStatus
from tests.scenarios.conftest import PRIYA, ScenarioEnv, reading
from tests.scenarios.test_run import AUG_END, AUG_START

KEVIN = "kevin@icon-technologies.com"


def notes(env: ScenarioEnv) -> list:  # type: ignore[type-arg]
    return [e for e in env.sender.sent_emails() if "couldn't tell which" in e.body]


class TestAnEmailThatAnswersNothing:
    def test_kevin_hears_nothing_was_done_and_what_to_do(self, env: ScenarioEnv) -> None:
        env.add_email(
            KEVIN,
            subject="the Acme one",
            attachment=None,
            body="Please bill Acme at the new rate from now on, thanks.",
        )
        env.run()

        (note,) = notes(env)
        assert note.to == (KEVIN,)
        assert note.subject == "Re: the Acme one"
        assert "haven't done anything with it" in note.body
        assert '"Please bill Acme at the new rate from now on, thanks."' in note.body
        assert "Reply to the email of mine it is about" in note.body
        assert env.store.open_reviews() == []

    def test_it_is_threaded_under_his_email(self, env: ScenarioEnv) -> None:
        env.add_email(KEVIN, subject="hello", attachment=None, message_id="<k1@icon>")
        env.run()
        record = next(r for r in env.store.outgoing_records() if r.kind == "unmatched_reply_email")
        assert record.payload["in_reply_to"] == "<k1@icon>"

    def test_once_however_many_runs(self, env: ScenarioEnv) -> None:
        env.add_email(KEVIN, subject="hello", attachment=None)
        env.run()
        env.run()
        assert len(notes(env)) == 1

    def test_a_long_email_is_quoted_only_in_part(self, env: ScenarioEnv) -> None:
        env.add_email(KEVIN, subject="long", attachment=None, body="word " * 400)
        env.run()
        (note,) = notes(env)
        assert '..."' in note.body
        assert len(note.body) < 1_200

    def test_an_attachment_gets_the_line_about_timesheets(self, env: ScenarioEnv) -> None:
        """Kevin forwarding a timesheet himself is not how one gets read."""
        env.add_email(KEVIN, subject="Fwd: Priya's August", body="see attached")
        env.run()
        (note,) = notes(env)
        assert "consultant's own address" in note.body


class TestAnAnswerToTheNote:
    def test_is_not_answered_again(self, env: ScenarioEnv) -> None:
        """An answer to the note -- or an out-of-office reply to it -- matches
        nothing either; answering that would go round for ever."""
        env.add_email(KEVIN, subject="hello", attachment=None)
        env.run()
        record = next(r for r in env.store.outgoing_records() if r.kind == "unmatched_reply_email")
        assert record.message_id

        env.add_email(
            KEVIN,
            subject="Re: hello",
            attachment=None,
            body="I am out of the office until Monday.",
            in_reply_to=record.message_id,
        )
        env.run()

        assert len(notes(env)) == 1


class TestRepliesThatDoMatch:
    def test_are_handled_as_before_with_no_note(self, env: ScenarioEnv) -> None:
        env.add_email(PRIYA, scripted_reading=reading(AUG_START, AUG_END, approved=False))
        env.run()
        review = next(s for s in env.sent_subjects() if s.startswith("Needs your review"))
        env.replies["ignore"] = ReplyReading(
            answers=[ReplyAnswer(review_code="NO_APPROVAL", kind=ReplyAnswerKind.IGNORE)]
        )
        env.reply_from_kevin(review, "ignore")
        env.run()

        assert notes(env) == []
        assert env.the_item().status is ItemStatus.IGNORED
