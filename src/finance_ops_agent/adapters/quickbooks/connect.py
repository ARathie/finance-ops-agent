"""`fops qbo-connect`: the one-time QuickBooks sign-in.

Opens Intuit's sign-in page, waits on a loopback callback for the redirect,
exchanges the code, and stores the realm id and the first token pair. Run with
Kevin present: he is the one who approves the connection to Icon's company.
"""

import secrets
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from finance_ops_agent.adapters.quickbooks.client import (
    AUTHORIZE_URL,
    SCOPE,
    QuickBooksReconnect,
    exchange_code,
)
from finance_ops_agent.adapters.quickbooks.tokens import Tokens, TokenStore, utcnow

DEFAULT_PORT = 8723  # loopback only, and only while the sign-in is happening


@dataclass
class Callback:
    code: str = ""
    realm_id: str = ""
    state: str = ""
    error: str = ""


def authorize_url(client_id: str, redirect_uri: str, state: str) -> str:
    query = urlencode(
        {
            "client_id": client_id,
            "response_type": "code",
            "scope": SCOPE,
            "redirect_uri": redirect_uri,
            "state": state,
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


def wait_for_callback(port: int, expected_state: str) -> Callback:
    """Serve exactly one request on loopback and read the redirect from it."""
    result = Callback()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - the name http.server requires
            query = parse_qs(urlparse(self.path).query)
            result.code = (query.get("code") or [""])[0]
            result.realm_id = (query.get("realmId") or [""])[0]
            result.state = (query.get("state") or [""])[0]
            result.error = (query.get("error") or [""])[0]
            ok = bool(result.code) and result.state == expected_state
            body = (
                b"QuickBooks is connected. You can close this tab."
                if ok
                else b"Something went wrong. Look at the terminal for details."
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return  # the CLI prints what matters

    with HTTPServer(("127.0.0.1", port), Handler) as server:
        server.handle_request()
    return result


def connect(
    store: TokenStore,
    client_id: str,
    client_secret: str,
    environment: str,
    port: int = DEFAULT_PORT,
    open_browser: bool = True,
    http: httpx.Client | None = None,
) -> Tokens:
    redirect_uri = f"http://localhost:{port}/callback"
    state = secrets.token_urlsafe(16)
    url = authorize_url(client_id, redirect_uri, state)
    print("Sign in to QuickBooks and approve the connection:")
    print(f"  {url}")
    print(f"(This redirect URI must be registered in the Intuit app: {redirect_uri})")
    if open_browser:
        webbrowser.open(url)

    callback = wait_for_callback(port, state)
    if callback.error:
        raise QuickBooksReconnect(f"QuickBooks reported: {callback.error}")
    if not callback.code:
        raise QuickBooksReconnect("no sign-in code came back; nothing was stored")
    if callback.state != state:
        # Someone else's redirect, not the one this command started.
        raise QuickBooksReconnect("the sign-in did not match this request; nothing stored")
    if not callback.realm_id:
        raise QuickBooksReconnect("QuickBooks did not say which company to use")

    payload = exchange_code(callback.code, redirect_uri, client_id, client_secret, http)
    now = utcnow()
    tokens = Tokens(
        realm_id=callback.realm_id,
        access_token=str(payload["access_token"]),
        refresh_token=str(payload["refresh_token"]),
        access_expires_at=now,  # replaced immediately below
        refreshed_at=now,
        environment=environment,
    ).rotated(
        access_token=str(payload["access_token"]),
        refresh_token=str(payload["refresh_token"]),
        expires_in=int(payload.get("expires_in", 3600)),
        now=now,
    )
    store.save(tokens)
    return tokens
