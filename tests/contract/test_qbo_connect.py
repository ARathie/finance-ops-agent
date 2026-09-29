"""`fops qbo-connect`: the one-time sign-in, and the ways it must refuse.

The state check is the CSRF protection on the redirect: the command makes a
random state, puts it in the authorize URL, and will not store anything unless
the redirect carries the same one back. Without it, a redirect from another tab
-- or one an attacker caused -- could connect the agent to a company nobody at
Icon chose. Nothing about that is visible in normal use, which is exactly why
it needs a test.
"""

import socket
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from finance_ops_agent.adapters.quickbooks import connect as connect_module
from finance_ops_agent.adapters.quickbooks.client import QuickBooksReconnect
from finance_ops_agent.adapters.quickbooks.connect import (
    Callback,
    authorize_url,
    connect,
    is_loopback,
    parse_redirect,
    wait_for_callback,
)
from finance_ops_agent.adapters.quickbooks.tokens import Tokens, TokenStore
from tests.contract.http_replay import Replay

TOKEN_URL = "POST https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"


def token_replay() -> Replay:
    return Replay(
        {
            TOKEN_URL: [
                {
                    "status": 200,
                    "json": {
                        "access_token": "an-access-token",
                        "refresh_token": "a-refresh-token",
                        "expires_in": 3600,
                    },
                }
            ]
        }
    )


def run_connect(store: TokenStore, callback: Callback, monkeypatch: pytest.MonkeyPatch) -> object:
    monkeypatch.setattr(connect_module, "wait_for_callback", lambda port, state: callback)
    return connect(
        store,
        client_id="a-client-id",
        client_secret="a-client-secret",
        environment="sandbox",
        open_browser=False,
        http=token_replay().client(),
    )


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
    return port


def serve_one(expected_state: str, query: str) -> tuple[bytes, list[Callback]]:
    """One request through the real listener, and what it made of it.

    The port is picked by asking the OS for a free one and then letting go of
    it, so now and again something else takes it before the listener binds.
    That is the test's own race, not the listener's, so the whole cycle is
    retried on a fresh port rather than failing.
    """
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({})
    )  # 127.0.0.1, never a proxy
    last: Exception | None = None
    for _ in range(5):
        port = free_port()
        seen: list[Callback] = []
        server = threading.Thread(
            target=lambda p=port, out=seen: out.append(wait_for_callback(p, expected_state)),
            daemon=True,
        )
        server.start()
        deadline = time.monotonic() + 2
        while True:
            try:
                with opener.open(f"http://127.0.0.1:{port}/callback?{query}", timeout=5) as reply:
                    body: bytes = reply.read()
                server.join(timeout=5)
                return body, seen
            except urllib.error.URLError as error:
                last = error
                if time.monotonic() > deadline:
                    break
                time.sleep(0.05)
    raise AssertionError(f"the listener never came up: {last}")


class TestTheAuthorizeUrl:
    def test_it_carries_the_state_and_asks_only_for_accounting(self) -> None:
        url = authorize_url("a-client-id", "http://localhost:8723/callback", "a-state")
        query = parse_qs(urlparse(url).query)
        assert query["state"] == ["a-state"]
        assert query["scope"] == ["com.intuit.quickbooks.accounting"]
        assert query["response_type"] == ["code"]


class TestRefusals:
    """Every one of these must store nothing at all."""

    def test_a_redirect_whose_state_does_not_match_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = TokenStore(tmp_path / "qbo_tokens.json")
        callback = Callback(code="a-code", realm_id="9130350000000", state="someone-elses-state")

        with pytest.raises(QuickBooksReconnect, match="did not match this request"):
            run_connect(store, callback, monkeypatch)
        assert not store.exists()

    def test_a_redirect_with_no_state_at_all_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = TokenStore(tmp_path / "qbo_tokens.json")
        callback = Callback(code="a-code", realm_id="9130350000000", state="")

        with pytest.raises(QuickBooksReconnect, match="did not match this request"):
            run_connect(store, callback, monkeypatch)
        assert not store.exists()

    def test_a_redirect_naming_no_company_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without a realm id there is no company to talk to, and guessing one
        would be worse than failing."""
        store = TokenStore(tmp_path / "qbo_tokens.json")

        def capture(port: int, state: str) -> Callback:
            return Callback(code="a-code", realm_id="", state=state)

        monkeypatch.setattr(connect_module, "wait_for_callback", capture)
        with pytest.raises(QuickBooksReconnect, match="did not say which company"):
            connect(
                store,
                client_id="a-client-id",
                client_secret="a-client-secret",
                environment="sandbox",
                open_browser=False,
                http=token_replay().client(),
            )
        assert not store.exists()

    def test_a_refusal_from_intuit_is_passed_on(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = TokenStore(tmp_path / "qbo_tokens.json")
        callback = Callback(error="access_denied")

        with pytest.raises(QuickBooksReconnect, match="access_denied"):
            run_connect(store, callback, monkeypatch)
        assert not store.exists()

    def test_a_redirect_with_no_code_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = TokenStore(tmp_path / "qbo_tokens.json")
        callback = Callback(code="", realm_id="9130350000000", state="a-state")

        with pytest.raises(QuickBooksReconnect, match="no sign-in code"):
            run_connect(store, callback, monkeypatch)
        assert not store.exists()


class TestASignInThatWorks:
    def test_the_matching_state_is_accepted_and_the_company_stored(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = TokenStore(tmp_path / "qbo_tokens.json")

        def matching(port: int, state: str) -> Callback:
            return Callback(code="a-code", realm_id="9130350000000", state=state)

        monkeypatch.setattr(connect_module, "wait_for_callback", matching)
        tokens = connect(
            store,
            client_id="a-client-id",
            client_secret="a-client-secret",
            environment="sandbox",
            open_browser=False,
            http=token_replay().client(),
        )

        assert tokens.realm_id == "9130350000000"
        assert tokens.refresh_token == "a-refresh-token"
        assert store.load().realm_id == "9130350000000"
        assert store.path.stat().st_mode & 0o077 == 0  # tokens are secrets


class TestTheLoopbackListener:
    """The real one-shot server, so the parsing is not only tested in theory."""

    def test_it_reads_the_redirect_and_answers_once(self) -> None:
        body, seen = serve_one("the-state", "code=a-code&realmId=913&state=the-state")

        assert b"QuickBooks is connected" in body
        assert seen[0] == Callback(code="a-code", realm_id="913", state="the-state")

    def test_a_mismatched_state_is_told_so_in_the_browser(self) -> None:
        body, _ = serve_one("the-state", "code=a-code&realmId=913&state=another-state")

        assert b"Something went wrong" in body


PUBLIC_CALLBACK = "https://icon.example/icon-legal/callback.html"


def connect_by_pasting(
    store: TokenStore, pasted: Callable[[str], str], environment: str = "production"
) -> Tokens:
    return connect(
        store,
        client_id="a-client-id",
        client_secret="a-client-secret",
        environment=environment,
        open_browser=False,
        http=token_replay().client(),
        redirect_uri=PUBLIC_CALLBACK,
        read_pasted=pasted,
    )


class TestAProductionSignIn:
    """A production app may not redirect to localhost (decision 51), so the
    person pastes the address the public callback page landed on."""

    def test_production_with_the_localhost_listener_is_refused_before_signing_in(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = TokenStore(tmp_path / "qbo_tokens.json")

        def never(port: int, state: str) -> Callback:
            raise AssertionError("no listener should have been started")

        monkeypatch.setattr(connect_module, "wait_for_callback", never)
        with pytest.raises(QuickBooksReconnect, match="QBO_REDIRECT_URI"):
            connect(
                store,
                client_id="a-client-id",
                client_secret="a-client-secret",
                environment="production",
                open_browser=False,
                http=token_replay().client(),
            )
        assert not store.exists()

    def test_the_pasted_address_is_read_and_the_company_stored(self, tmp_path: Path) -> None:
        store = TokenStore(tmp_path / "qbo_tokens.json")
        asked: list[str] = []

        def paste(prompt: str) -> str:
            asked.append(prompt)
            state = parse_qs(urlparse(asked_url[0]).query)["state"][0]
            return f"  {PUBLIC_CALLBACK}?code=a-code&state={state}&realmId=9341450000000  "

        asked_url: list[str] = []
        original = connect_module.authorize_url

        def remember(client_id: str, redirect_uri: str, state: str) -> str:
            url = original(client_id, redirect_uri, state)
            asked_url.append(url)
            return url

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(connect_module, "authorize_url", remember)
            tokens = connect_by_pasting(store, paste)

        assert asked, "the person was asked to paste the address"
        assert parse_qs(urlparse(asked_url[0]).query)["redirect_uri"] == [PUBLIC_CALLBACK]
        assert tokens.environment == "production"
        assert store.load().realm_id == "9341450000000"
        assert store.load().environment == "production"

    def test_a_pasted_address_from_another_sign_in_is_refused(self, tmp_path: Path) -> None:
        store = TokenStore(tmp_path / "qbo_tokens.json")

        def paste(prompt: str) -> str:
            return f"{PUBLIC_CALLBACK}?code=a-code&state=an-old-state&realmId=934"

        with pytest.raises(QuickBooksReconnect, match="did not match this request"):
            connect_by_pasting(store, paste)
        assert not store.exists()

    def test_pasting_something_that_is_not_the_address_is_refused(self, tmp_path: Path) -> None:
        store = TokenStore(tmp_path / "qbo_tokens.json")

        with pytest.raises(QuickBooksReconnect, match="no sign-in code"):
            connect_by_pasting(store, lambda prompt: "I closed the tab")
        assert not store.exists()

    def test_a_refusal_in_the_pasted_address_is_passed_on(self, tmp_path: Path) -> None:
        store = TokenStore(tmp_path / "qbo_tokens.json")

        with pytest.raises(QuickBooksReconnect, match="access_denied"):
            connect_by_pasting(
                store, lambda prompt: f"{PUBLIC_CALLBACK}?error=access_denied&state=x"
            )
        assert not store.exists()


class TestParsingTheRedirect:
    def test_it_reads_every_field(self) -> None:
        assert parse_redirect("https://a.example/cb?code=c&realmId=9&state=s") == Callback(
            code="c", realm_id="9", state="s"
        )

    @pytest.mark.parametrize(
        ("uri", "loopback"),
        [
            ("http://localhost:8723/callback", True),
            ("http://127.0.0.1:8723/callback", True),
            ("https://icon.example/icon-legal/callback.html", False),
        ],
    )
    def test_which_redirects_are_loopback(self, uri: str, loopback: bool) -> None:
        assert is_loopback(uri) is loopback
