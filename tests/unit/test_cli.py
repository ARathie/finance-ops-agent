import re

import pytest

from finance_ops_agent import __version__
from finance_ops_agent.cli.main import main


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
