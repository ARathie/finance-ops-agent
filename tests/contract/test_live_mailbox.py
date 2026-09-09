"""The one test that touches the real mailbox: off unless FOPS_LIVE_TESTS=1.

It runs `fops doctor --send-test-email` with the settings in the environment
(or `.env`), so it logs in over IMAP and SMTP, lists the folders, and sends
exactly one email, to Kevin. It never touches a client address. Run it by
hand before the first real dry run; CI never sets the variable.
"""

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("FOPS_LIVE_TESTS") != "1",
    reason="set FOPS_LIVE_TESTS=1 to touch the real mailbox",
)


def test_the_real_mailbox_works(capsys: pytest.CaptureFixture[str]) -> None:
    from finance_ops_agent.cli.main import main

    code = main(["doctor", "--send-test-email"])
    out = capsys.readouterr().out
    print(out)
    assert code == 0, out
    assert "Nothing was sent to a client." in out
