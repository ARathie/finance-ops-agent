"""QuickBooks tokens on disk, rotated safely.

Refresh tokens rotate on use: Intuit hands back a new one with every refresh
and the old one stops working. So the new token is written down *before* the
new access token is used, and written atomically (temp file, then rename), or
a crash mid-write would leave the agent unable to reconnect without Kevin.

The file also carries when the last successful refresh happened, so the agent
can warn before the refresh token ages out (about 100 days unused).
"""

import json
import os
import stat
import tempfile
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

REFRESH_TOKEN_LIFETIME_DAYS = 100
WARN_AFTER_DAYS = 80


class NotConnected(Exception):
    """There are no stored tokens; `fops qbo-connect` has not been run."""


@dataclass(frozen=True)
class Tokens:
    realm_id: str
    access_token: str
    refresh_token: str
    access_expires_at: datetime
    refreshed_at: datetime
    environment: str = "sandbox"

    def access_token_is_stale(self, now: datetime, margin_seconds: int = 300) -> bool:
        """True when the access token has expired, or is about to."""
        return now + timedelta(seconds=margin_seconds) >= self.access_expires_at

    def days_since_refresh(self, now: datetime) -> int:
        return (now - self.refreshed_at).days

    def refresh_token_warning(self, now: datetime) -> str | None:
        days = self.days_since_refresh(now)
        if days < WARN_AFTER_DAYS:
            return None
        left = REFRESH_TOKEN_LIFETIME_DAYS - days
        if left <= 0:
            return "The QuickBooks connection has expired. Run `fops qbo-connect` to reconnect."
        return (
            f"The QuickBooks connection has not been refreshed for {days} days and"
            f" stops working in about {left}. Run `fops qbo-connect` to renew it."
        )

    def to_json(self) -> str:
        return json.dumps(
            {
                "realm_id": self.realm_id,
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
                "access_expires_at": self.access_expires_at.isoformat(),
                "refreshed_at": self.refreshed_at.isoformat(),
                "environment": self.environment,
            },
            indent=2,
        )

    @classmethod
    def from_json(cls, text: str) -> "Tokens":
        raw = json.loads(text)
        return cls(
            realm_id=str(raw["realm_id"]),
            access_token=str(raw["access_token"]),
            refresh_token=str(raw["refresh_token"]),
            access_expires_at=datetime.fromisoformat(raw["access_expires_at"]),
            refreshed_at=datetime.fromisoformat(raw["refreshed_at"]),
            environment=str(raw.get("environment", "sandbox")),
        )

    def rotated(
        self, access_token: str, refresh_token: str, expires_in: int, now: datetime
    ) -> "Tokens":
        return replace(
            self,
            access_token=access_token,
            refresh_token=refresh_token,
            access_expires_at=now + timedelta(seconds=expires_in),
            refreshed_at=now,
        )


class TokenStore:
    """`data/qbo_tokens.json`, owner-readable only."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def exists(self) -> bool:
        return self.path.exists()

    def load(self) -> Tokens:
        if not self.path.exists():
            raise NotConnected(
                f"{self.path} does not exist. Run `fops qbo-connect` to connect QuickBooks."
            )
        return Tokens.from_json(self.path.read_text())

    def save(self, tokens: Tokens) -> None:
        """Write atomically so a crash never leaves a half-written token file."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(dir=self.path.parent, text=True)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as out:
                out.write(tokens.to_json())
                out.flush()
                os.fsync(out.fileno())
            temporary.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600: tokens are secrets
            temporary.replace(self.path)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise


def utcnow() -> datetime:
    return datetime.now(UTC)
