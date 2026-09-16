import re
from argparse import Namespace
from datetime import date
from pathlib import Path

import pytest

from finance_ops_agent import __version__
from finance_ops_agent.cli.main import _usage_report, build_parser, main
from finance_ops_agent.ports.reader import TokenUsage


def test_version_is_semver() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__)


def test_fops_version_prints_a_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert out.strip() == f"fops {__version__}"


def test_fops_without_arguments_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    main([])
    out = capsys.readouterr().out
    assert "usage: fops" in out


def eval_args(price_input: int | None = None, price_output: int | None = None) -> Namespace:
    return Namespace(price_input=price_input, price_output=price_output)


def test_eval_costs_a_live_run_from_the_built_in_price_table() -> None:
    report = _usage_report(
        TokenUsage(requests=1, input_tokens=1_000_000), 1, "claude-opus-5", eval_args()
    )
    assert report.total_millicents() == 500_000  # $5.00 per million input tokens


def test_eval_says_it_has_no_price_for_a_model_it_does_not_know() -> None:
    report = _usage_report(
        TokenUsage(requests=1, input_tokens=1_000_000), 1, "claude-some-future-model", eval_args()
    )
    assert report.total_millicents() is None


def test_given_prices_override_the_built_in_table_when_it_goes_stale() -> None:
    report = _usage_report(
        TokenUsage(requests=1, input_tokens=1_000_000, output_tokens=1_000_000),
        1,
        "claude-opus-5",
        eval_args(price_input=300, price_output=1_500),
    )
    assert report.total_millicents() == 300_000 + 1_500_000


def test_overridden_prices_carry_the_cache_rates_with_them() -> None:
    """Cache writes are 1.25x input and cache reads 0.1x, whatever input costs."""
    report = _usage_report(
        TokenUsage(
            requests=1, cache_creation_input_tokens=1_000_000, cache_read_input_tokens=1_000_000
        ),
        1,
        "claude-opus-5",
        eval_args(price_input=400, price_output=2_000),
    )
    assert report.total_millicents() == 500_000 + 40_000


def test_one_price_without_the_other_is_ignored_rather_than_half_applied() -> None:
    report = _usage_report(
        TokenUsage(requests=1, input_tokens=1_000_000), 1, "claude-opus-5", eval_args(price_input=1)
    )
    assert report.total_millicents() == 500_000  # still the built-in price


def test_eval_accepts_the_price_flags() -> None:
    args = build_parser().parse_args(["eval", "--price-input", "500", "--price-output", "2500"])
    assert (args.price_input, args.price_output) == (500, 2500)
    assert build_parser().parse_args(["eval"]).price_input is None


def test_since_is_accepted_by_run_and_dry_run() -> None:
    """Both take it: a real `fops dry-run` is `fops run` in dry-run mode."""
    from finance_ops_agent.cli.main import build_parser

    parser = build_parser()
    for command in ("run", "dry-run"):
        args = parser.parse_args([command, "--since", "2026-09-09"])
        assert args.since == date(2026, 9, 9)


def test_the_top_level_parser_still_owns_its_own_options() -> None:
    """--version belongs to `fops`, not to a subcommand. A loop adding an
    option to several subparsers once rebound the name `parser` and quietly
    moved it."""
    from finance_ops_agent.cli.main import build_parser

    with pytest.raises(SystemExit) as exit_code:
        build_parser().parse_args(["--version"])
    assert exit_code.value.code == 0


def test_reading_again_from_a_date_forgets_how_far_it_had_got(tmp_path: Path) -> None:
    """--since moves two things, not one: the date, and the position the agent
    had reached. Without the second, mail it has already walked past stays
    invisible however far back the date goes."""
    from finance_ops_agent.adapters.email.inbox import decode_position, encode_position
    from finance_ops_agent.application.run import MAILBOX_POSITION_KEY
    from finance_ops_agent.cli.main import _open_store

    store = _open_store(tmp_path)
    store.set_state(MAILBOX_POSITION_KEY, encode_position(uidvalidity=7, last_uid=42))
    assert decode_position(store.get_state(MAILBOX_POSITION_KEY), 7) == 42

    store.set_state(MAILBOX_POSITION_KEY, "")  # what --since does
    assert decode_position(store.get_state(MAILBOX_POSITION_KEY), 7) == 0
