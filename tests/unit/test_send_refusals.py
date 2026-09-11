"""What the agent says when the mail server refuses a recipient.

These run with no server. The address alone does not tell whoever reads
`fops doctor` or a SEND_FAILED review what to do next: a wrong address and a
mailbox that is not allowed to send there need different fixes, and only the
server's own reply tells them apart.
"""

from finance_ops_agent.adapters.email.sender import describe_refusals


def test_the_reason_the_server_gave_is_kept() -> None:
    described = describe_refusals({"kevin@icon-technologies.com": (550, b"No such user here")})
    assert described == "kevin@icon-technologies.com (550 No such user here)"


def test_every_refused_address_is_named_in_a_steady_order() -> None:
    described = describe_refusals(
        {
            "zoe@acme.example": (550, b"no such user"),
            "adam@acme.example": (552, b"over quota"),
        }
    )
    assert described == "adam@acme.example (552 over quota); zoe@acme.example (550 no such user)"


def test_a_server_that_gave_no_reason_still_gives_its_code() -> None:
    assert describe_refusals({"kevin@icon-technologies.com": (571, b"  ")}) == (
        "kevin@icon-technologies.com (571)"
    )


def test_a_reason_that_is_not_utf8_does_not_crash_the_run() -> None:
    described = describe_refusals({"kevin@icon-technologies.com": (550, b"caf\xe9 closed")})
    assert described.startswith("kevin@icon-technologies.com (550 caf")
