"""Entry point for the fops command."""

import argparse

from finance_ops_agent import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fops",
        description="Billing agent for Icon Technologies (developer/operator command line).",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"fops {__version__}",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    parser.parse_args(argv)
    # No subcommands yet; run/dry-run/status/doctor/eval/backup arrive in later PRs.
    parser.print_help()


if __name__ == "__main__":
    main()
