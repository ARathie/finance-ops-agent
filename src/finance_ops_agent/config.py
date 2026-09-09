"""Settings from environment variables (a .env file locally).

The names and defaults are the ones in docs/technical-design.md. Nothing here
reads a rate or a contact: those come only from the engagement list.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from finance_ops_agent.application.context import Mode


class MissingSettingError(Exception):
    """A setting the agent cannot run without is not set."""


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise MissingSettingError(f"{name} is not set")
    return value


@dataclass(frozen=True)
class MicrosoftSettings:
    tenant_id: str
    client_id: str
    client_secret: str
    mailbox: str

    @classmethod
    def from_env(cls) -> "MicrosoftSettings":
        return cls(
            tenant_id=_required("MS_TENANT_ID"),
            client_id=_required("MS_CLIENT_ID"),
            client_secret=_required("MS_CLIENT_SECRET"),
            mailbox=_required("FOPS_AGENT_MAILBOX"),
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
            accounting=os.environ.get("FOPS_ACCOUNTING", "manual"),
        )
