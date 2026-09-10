"""`.env.example` is what an operator copies to `.env` (docs/running-it.md).

It is loaded by launchd, by `env_file` in docker-compose, by systemd, and —
when someone is setting up by hand — by sourcing it into a shell. A value
containing a space has to be quoted or the shell splits it and tries to run
the rest as a command, which is confusing and easy to miss.
"""

import re
import subprocess
from pathlib import Path

EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"
SETTING = re.compile(r"^(?P<name>[A-Z][A-Z0-9_]*)=(?P<value>.*)$")


def settings() -> list[tuple[str, str]]:
    found = []
    for line in EXAMPLE.read_text().splitlines():
        match = SETTING.match(line)
        if match:
            found.append((match["name"], match["value"]))
    return found


def test_it_is_committed_and_has_settings() -> None:
    assert EXAMPLE.exists()
    assert len(settings()) > 20


def test_every_value_with_a_space_is_quoted() -> None:
    unquoted = [
        name
        for name, value in settings()
        if " " in value and not (value.startswith(('"', "'")) and value[-1] == value[0])
    ]
    assert unquoted == [], f"quote these values in .env.example: {unquoted}"


def test_sourcing_it_into_a_shell_produces_no_errors() -> None:
    """The check that catches it in practice: source the file, keep stderr."""
    result = subprocess.run(
        ["bash", "-c", f"set -a; . {EXAMPLE}; set +a"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == "", result.stderr


def test_the_quotes_do_not_become_part_of_the_value() -> None:
    """A quoted value is unquoted by every loader, so the app sees the text."""
    result = subprocess.run(
        ["bash", "-c", f'set -a; . {EXAMPLE}; set +a; printf "%s" "$QBO_ITEM_NAME"'],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.stdout == "Consulting Services"


def test_it_names_the_key_that_reads_timesheets() -> None:
    names = [name for name, _ in settings()]
    assert "ANTHROPIC_API_KEY" in names
    assert "FOPS_MODEL" in names
