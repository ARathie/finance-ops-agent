"""Reading `.env` when the agent is run by hand.

In production the settings arrive as real environment variables (launchd,
`env_file`, systemd). Run from a terminal nothing does that, so the command
reads `.env` from the folder it is in — without ever overriding what the
environment already says.
"""

from pathlib import Path

from finance_ops_agent.config import load_env_file, parse_env_file


def test_it_reads_names_and_values() -> None:
    parsed = parse_env_file("FOPS_TIMEZONE=America/New_York\nFOPS_MODE=dry_run\n")
    assert parsed == {"FOPS_TIMEZONE": "America/New_York", "FOPS_MODE": "dry_run"}


def test_blank_lines_and_comments_are_skipped() -> None:
    parsed = parse_env_file("# a heading\n\n   \n# FOPS_MODE=auto\nFOPS_MODE=dry_run\n")
    assert parsed == {"FOPS_MODE": "dry_run"}


def test_a_quoted_value_keeps_its_spaces_and_loses_its_quotes() -> None:
    parsed = parse_env_file('QBO_ITEM_NAME="Consulting Services"\n')
    assert parsed == {"QBO_ITEM_NAME": "Consulting Services"}
    assert parse_env_file("A='single quoted'\n") == {"A": "single quoted"}


def test_a_hash_inside_quotes_is_part_of_the_value() -> None:
    parsed = parse_env_file('MAIL_PASSWORD="pass # word"\n')
    assert parsed == {"MAIL_PASSWORD": "pass # word"}


def test_a_hash_in_an_unquoted_password_is_kept() -> None:
    """A password is not a comment. Only ` #` after whitespace starts one."""
    assert parse_env_file("MAIL_PASSWORD=pa55#w0rd\n") == {"MAIL_PASSWORD": "pa55#w0rd"}


def test_a_trailing_comment_after_a_space_is_dropped() -> None:
    assert parse_env_file("FOPS_MODE=dry_run  # the stop button\n") == {"FOPS_MODE": "dry_run"}


def test_an_export_prefix_is_allowed() -> None:
    assert parse_env_file("export FOPS_MODE=dry_run\n") == {"FOPS_MODE": "dry_run"}


def test_an_empty_value_is_read_as_empty() -> None:
    assert parse_env_file("ANTHROPIC_API_KEY=\n") == {"ANTHROPIC_API_KEY": ""}


def test_a_value_may_contain_an_equals_sign() -> None:
    assert parse_env_file("A=b=c\n") == {"A": "b=c"}


def test_loading_fills_gaps_in_the_environment(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("FOPS_TIMEZONE=America/New_York\n")
    environ: dict[str, str] = {}
    assert load_env_file(path, environ) == ["FOPS_TIMEZONE"]
    assert environ["FOPS_TIMEZONE"] == "America/New_York"


def test_the_environment_always_beats_the_file(tmp_path: Path) -> None:
    """`FOPS_MODE=dry_run fops run` must still be the stop button."""
    path = tmp_path / ".env"
    path.write_text("FOPS_MODE=auto\n")
    environ = {"FOPS_MODE": "dry_run"}
    assert load_env_file(path, environ) == []
    assert environ["FOPS_MODE"] == "dry_run"


def test_no_file_is_not_an_error(tmp_path: Path) -> None:
    environ: dict[str, str] = {}
    assert load_env_file(tmp_path / "nothing-here", environ) == []
    assert environ == {}


def test_the_committed_example_loads(tmp_path: Path) -> None:
    """The file an operator copies must parse, and name what it promises."""
    example = Path(__file__).resolve().parents[2] / ".env.example"
    environ: dict[str, str] = {}
    load_env_file(example, environ)
    assert environ["FOPS_TIMEZONE"] == "America/New_York"
    assert environ["FOPS_MODE"] == "dry_run"
    assert environ["QBO_ITEM_NAME"] == "Consulting Services"
    assert environ["ANTHROPIC_API_KEY"] == ""
