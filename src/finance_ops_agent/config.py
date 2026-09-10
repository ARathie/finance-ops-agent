"""Settings from environment variables (a .env file locally).

The names and defaults are the ones in docs/technical-design.md. Nothing here
reads a rate or a contact: those come only from the engagement list.

In production the settings arrive as real environment variables: launchd's
`EnvironmentVariables`, `env_file` in docker-compose, systemd's
`EnvironmentFile` (docs/running-it.md). Run by hand, nothing would have put
them there, so `load_env_file` reads `.env` from the folder the command runs
in. A variable already in the environment always wins, so `FOPS_MODE=dry_run
fops run` still overrides the file, and a container's own settings are never
replaced by a stray `.env`.
"""

import os
from collections.abc import MutableMapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from finance_ops_agent.application.context import Mode

ENV_FILE = ".env"


class MissingSettingError(Exception):
    """A setting the agent cannot run without is not set."""


def parse_env_file(text: str) -> dict[str, str]:
    """The `.env` format described in `.env.example`.

    Blank lines and `#` comments are skipped, an `export ` prefix is allowed,
    and a value wrapped in a matching pair of quotes keeps everything inside
    them (including a `#`). In an unquoted value a `#` that follows whitespace
    starts a comment; one inside the text, as in a password, does not.
    """
    settings: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        name = name.removeprefix("export ").strip()
        if not name:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        else:
            for marker in (" #", "\t#"):
                cut = value.find(marker)
                if cut != -1:
                    value = value[:cut].rstrip()
        settings[name] = value
    return settings


def load_env_file(path: Path, environ: MutableMapping[str, str] | None = None) -> list[str]:
    """Put a `.env` file's settings into the environment, and say which.

    A name already set in the environment is left alone: the file fills gaps,
    it never overrides what the machine or the command line already said.
    """
    target = os.environ if environ is None else environ
    if not path.is_file():
        return []
    loaded = []
    for name, value in parse_env_file(path.read_text()).items():
        if name not in target:
            target[name] = value
            loaded.append(name)
    return loaded


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise MissingSettingError(f"{name} is not set")
    return value


LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
SECURITY_CHOICES = ("ssl", "starttls", "none")


def _security(name: str, host: str, default: str) -> str:
    value = os.environ.get(name, default).strip().lower()
    if value not in SECURITY_CHOICES:
        raise MissingSettingError(f"{name} should be ssl, starttls, or none, not {value!r}")
    if value == "none" and host not in LOCAL_HOSTS:
        raise MissingSettingError(
            f"{name}=none is only for a test server on this machine, not for {host}"
        )
    return value


def _port(name: str, default: int) -> int:
    text = os.environ.get(name, "").strip()
    if not text:
        return default
    try:
        return int(text)
    except ValueError as error:
        raise MissingSettingError(f"{name} should be a port number, not {text!r}") from error


@dataclass(frozen=True)
class MailSettings:
    """The agent's own mailbox at Rackspace Email: IMAP to read, SMTP to send
    (docs/integrations/email-imap-smtp.md)."""

    imap_host: str
    imap_port: int
    imap_security: str
    smtp_host: str
    smtp_port: int
    smtp_security: str
    username: str
    password: str
    start_date: date | None  # None = from the first run onwards
    folder_prefix: str
    sent_folder: str | None

    @classmethod
    def from_env(cls) -> "MailSettings":
        imap_host = os.environ.get("MAIL_IMAP_HOST", "secure.emailsrvr.com").strip()
        smtp_host = os.environ.get("MAIL_SMTP_HOST", "secure.emailsrvr.com").strip()
        start_text = os.environ.get("MAIL_START_DATE", "").strip()
        try:
            start_date = date.fromisoformat(start_text) if start_text else None
        except ValueError as error:
            raise MissingSettingError(
                f"MAIL_START_DATE should be a date like 2026-09-09, not {start_text!r}"
            ) from error
        return cls(
            imap_host=imap_host,
            imap_port=_port("MAIL_IMAP_PORT", 993),
            imap_security=_security("MAIL_IMAP_SECURITY", imap_host, "ssl"),
            smtp_host=smtp_host,
            smtp_port=_port("MAIL_SMTP_PORT", 465),
            smtp_security=_security("MAIL_SMTP_SECURITY", smtp_host, "ssl"),
            username=_required("MAIL_USERNAME"),
            password=_required("MAIL_PASSWORD"),
            start_date=start_date,
            folder_prefix=os.environ.get("MAIL_FOLDER_PREFIX", "Agent").strip() or "Agent",
            sent_folder=os.environ.get("MAIL_SENT_FOLDER", "").strip() or None,
        )


@dataclass(frozen=True)
class QuickBooksSettings:
    client_id: str
    client_secret: str
    environment: str  # sandbox | production
    item_name: str

    @classmethod
    def from_env(cls) -> "QuickBooksSettings":
        environment = os.environ.get("QBO_ENVIRONMENT", "sandbox").strip()
        if environment not in ("sandbox", "production"):
            raise MissingSettingError(
                f"QBO_ENVIRONMENT should be sandbox or production, not {environment!r}"
            )
        return cls(
            client_id=_required("QBO_CLIENT_ID"),
            client_secret=_required("QBO_CLIENT_SECRET"),
            environment=environment,
            item_name=os.environ.get("QBO_ITEM_NAME", "Consulting Services"),
        )


@dataclass(frozen=True)
class Config:
    mode: Mode
    timezone: str
    data_dir: Path
    engagement_list: Path
    admin_email: str
    agent_mailbox: str
    model: str
    accounting: str  # manual | quickbooks

    @property
    def qbo_token_path(self) -> Path:
        return self.data_dir / "qbo_tokens.json"

    @classmethod
    def from_env(cls) -> "Config":
        mode_text = os.environ.get("FOPS_MODE", Mode.DRY_RUN.value).strip()
        try:
            mode = Mode(mode_text)
        except ValueError as error:
            choices = ", ".join(item.value for item in Mode)
            raise MissingSettingError(
                f"FOPS_MODE should be one of {choices}, not {mode_text!r}"
            ) from error
        accounting = os.environ.get("FOPS_ACCOUNTING", "manual").strip()
        if accounting not in ("manual", "quickbooks"):
            raise MissingSettingError(
                f"FOPS_ACCOUNTING should be manual or quickbooks, not {accounting!r}"
            )
        return cls(
            mode=mode,
            # No default: period boundaries and due dates depend on it
            # (docs/open-questions.md).
            timezone=_required("FOPS_TIMEZONE"),
            data_dir=Path(os.environ.get("FOPS_DATA_DIR", "data")),
            engagement_list=Path(_required("FOPS_ENGAGEMENT_LIST")),
            admin_email=_required("FOPS_ADMIN_EMAIL"),
            agent_mailbox=_required("FOPS_AGENT_MAILBOX"),
            model=os.environ.get("FOPS_MODEL", "claude-opus-5"),
            accounting=accounting,
        )
