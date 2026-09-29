"""Moving to the production QuickBooks app (docs/decisions.md #51).

A production app cannot redirect to localhost, so the redirect address becomes
a setting; and tokens from the sandbox keys are no use to the production ones,
so `fops doctor` says so rather than reporting a healthy connection to the
wrong company.
"""

from datetime import timedelta
from pathlib import Path

import pytest

from finance_ops_agent.adapters.quickbooks.tokens import Tokens, TokenStore, utcnow
from finance_ops_agent.cli.doctor import CheckResult, check_quickbooks_tokens
from finance_ops_agent.config import MissingSettingError, QuickBooksSettings


@pytest.fixture
def keys(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    monkeypatch.setenv("QBO_CLIENT_ID", "a-client-id")
    monkeypatch.setenv("QBO_CLIENT_SECRET", "a-client-secret")
    monkeypatch.delenv("QBO_REDIRECT_URI", raising=False)
    monkeypatch.setenv("QBO_ENVIRONMENT", "production")
    return monkeypatch


class TestTheRedirectSetting:
    def test_blank_means_the_localhost_listener(self, keys: pytest.MonkeyPatch) -> None:
        assert QuickBooksSettings.from_env().redirect_uri == ""

    def test_an_https_address_is_kept(self, keys: pytest.MonkeyPatch) -> None:
        keys.setenv("QBO_REDIRECT_URI", " https://icon.example/icon-legal/callback.html ")
        settings = QuickBooksSettings.from_env()
        assert settings.redirect_uri == "https://icon.example/icon-legal/callback.html"
        assert settings.environment == "production"

    def test_a_plain_http_address_is_refused(self, keys: pytest.MonkeyPatch) -> None:
        keys.setenv("QBO_REDIRECT_URI", "http://icon.example/callback.html")
        with pytest.raises(MissingSettingError, match="https"):
            QuickBooksSettings.from_env()


def stored(tmp_path: Path, environment: str) -> TokenStore:
    now = utcnow()
    store = TokenStore(tmp_path / "qbo_tokens.json")
    store.save(
        Tokens(
            realm_id="9341450000000",
            access_token="an-access-token",
            refresh_token="a-refresh-token",
            access_expires_at=now + timedelta(hours=1),
            refreshed_at=now,
            environment=environment,
        )
    )
    return store


class TestTheDoctorsConnectionCheck:
    def test_sandbox_tokens_under_production_keys_fail(self, tmp_path: Path) -> None:
        check = check_quickbooks_tokens(stored(tmp_path, "sandbox"), "production")
        assert check.result is CheckResult.FAIL
        assert "QBO_ENVIRONMENT is production" in check.detail
        assert "qbo-connect" in check.detail

    def test_a_matching_connection_passes(self, tmp_path: Path) -> None:
        check = check_quickbooks_tokens(stored(tmp_path, "production"), "production")
        assert check.result is CheckResult.PASS
        assert "production company 9341450000000" in check.detail
