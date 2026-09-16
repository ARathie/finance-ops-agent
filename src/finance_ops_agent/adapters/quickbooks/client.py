"""The QuickBooks Online client: tokens, refresh, retries, and raw calls.

Error handling follows docs/integrations/quickbooks-online.md: 401 refreshes
once and then asks Kevin to reconnect; 429 and 5xx retry with backoff up to
three attempts; a 400 is a validation problem and fails at once with the
message QuickBooks gave, which is usually a missing customer or item.
"""

import base64
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any
from urllib.parse import quote

import httpx

from finance_ops_agent import logs
from finance_ops_agent.adapters.quickbooks.tokens import Tokens, TokenStore, utcnow
from finance_ops_agent.ports.accounting import AccountingFailed, AccountingNeedsReconnect

# Intuit puts a trace id on every response. Their support asks for it first
# when anything is wrong, so it is logged on every call and repeated in any
# failure the agent reports.
TRACE_HEADER = "intuit_tid"
PRODUCTION_BASE = "https://quickbooks.api.intuit.com"
SANDBOX_BASE = "https://sandbox-quickbooks.api.intuit.com"
TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
AUTHORIZE_URL = "https://appcenter.intuit.com/connect/oauth2"
SCOPE = "com.intuit.quickbooks.accounting"
MAX_TRIES = 3


class QuickBooksFailed(AccountingFailed):
    """A QuickBooks call failed (becomes QUICKBOOKS_FAILED)."""


class QuickBooksReconnect(AccountingNeedsReconnect):
    """The connection is no longer usable (becomes QUICKBOOKS_RECONNECT)."""


def trace_id(response: httpx.Response) -> str:
    return str(response.headers.get(TRACE_HEADER, ""))


def with_trace(message: str, tid: str) -> str:
    """Intuit's support team asks for this id, so Kevin should be able to read
    it off the email rather than go looking in a log."""
    return f"{message} (QuickBooks reference {tid})" if tid else message


def base_url(environment: str) -> str:
    return SANDBOX_BASE if environment != "production" else PRODUCTION_BASE


def basic_auth(client_id: str, client_secret: str) -> str:
    pair = f"{client_id}:{client_secret}".encode()
    return "Basic " + base64.standard_b64encode(pair).decode("ascii")


def exchange_code(
    code: str,
    redirect_uri: str,
    client_id: str,
    client_secret: str,
    http: httpx.Client | None = None,
) -> dict[str, Any]:
    """Turn the code from the sign-in redirect into the first token pair."""
    client = http or httpx.Client(timeout=30.0)
    response = client.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        },
        headers={
            "Authorization": basic_auth(client_id, client_secret),
            "Accept": "application/json",
        },
    )
    if response.status_code >= 400:
        raise QuickBooksReconnect(
            with_trace(
                f"exchanging the sign-in code failed ({response.status_code}):"
                f" {response.text[:300]}",
                trace_id(response),
            )
        )
    payload: dict[str, Any] = response.json()
    return payload


class QuickBooksClient:
    def __init__(
        self,
        store: TokenStore,
        client_id: str,
        client_secret: str,
        http: httpx.Client | None = None,
        now: Callable[[], datetime] = utcnow,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._store = store
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = http or httpx.Client(timeout=60.0)
        self._now = now
        self._sleep = sleep
        self._tokens: Tokens | None = None
        # The trace id from the most recent response, for anything that fails
        # after the call itself succeeded (a total that disagrees, say).
        self.last_intuit_tid: str = ""

    @property
    def tokens(self) -> Tokens:
        if self._tokens is None:
            self._tokens = self._store.load()
        return self._tokens

    @property
    def realm_id(self) -> str:
        return self.tokens.realm_id

    def company_url(self, suffix: str) -> str:
        base = base_url(self.tokens.environment)
        return f"{base}/v3/company/{self.realm_id}{suffix}"

    def refresh(self) -> Tokens:
        """Refresh, storing the rotated refresh token before using the new access token."""
        current = self.tokens
        response = self._http.post(
            TOKEN_URL,
            data={"grant_type": "refresh_token", "refresh_token": current.refresh_token},
            headers={
                "Authorization": basic_auth(self._client_id, self._client_secret),
                "Accept": "application/json",
            },
        )
        self.last_intuit_tid = trace_id(response)
        if response.status_code >= 400:
            logs.log(
                "quickbooks would not renew the connection",
                status=response.status_code,
                said=response.text[:400],
                intuit_tid=self.last_intuit_tid,
            )
            raise QuickBooksReconnect(
                with_trace(
                    "QuickBooks would not renew the connection"
                    f" ({response.status_code}). Run `fops qbo-connect` to reconnect.",
                    self.last_intuit_tid,
                )
            )
        payload = response.json()
        rotated = current.rotated(
            access_token=str(payload["access_token"]),
            # Intuit rotates the refresh token; keep the new one, and if it did
            # not send one, keep what we have rather than losing the connection.
            refresh_token=str(payload.get("refresh_token") or current.refresh_token),
            expires_in=int(payload.get("expires_in", 3600)),
            now=self._now(),
        )
        self._store.save(rotated)  # written down before it is used
        self._tokens = rotated
        return rotated

    def _access_token(self) -> str:
        tokens = self.tokens
        if tokens.access_token_is_stale(self._now()):
            tokens = self.refresh()
        return tokens.access_token

    def request(
        self,
        method: str,
        url: str,
        *,
        json: Any = None,
        accept: str = "application/json",
    ) -> Any:
        refreshed = False
        for attempt in range(1, MAX_TRIES + 1):
            headers = {
                "Authorization": f"Bearer {self._access_token()}",
                "Accept": accept,
            }
            response = self._http.request(method, url, json=json, headers=headers)
            self.last_intuit_tid = trace_id(response)
            logs.log(
                "quickbooks call",
                method=method,
                url=url,
                status=response.status_code,
                attempt=attempt,
                intuit_tid=self.last_intuit_tid,
            )
            if response.status_code == 401 and not refreshed:
                logs.log("quickbooks refused the token; renewing it once", url=url)
                self.refresh()  # one refresh, then give up and ask Kevin
                refreshed = True
                continue
            if response.status_code == 401:
                raise QuickBooksReconnect(
                    with_trace(
                        "QuickBooks refused the connection even after renewing it."
                        " Run `fops qbo-connect` to reconnect.",
                        self.last_intuit_tid,
                    )
                )
            if response.status_code in (429, 500, 502, 503, 504) and attempt < MAX_TRIES:
                logs.log(
                    "quickbooks was busy; trying again",
                    method=method,
                    url=url,
                    status=response.status_code,
                    attempt=attempt,
                    intuit_tid=self.last_intuit_tid,
                )
                self._sleep(2.0 * attempt)
                continue
            if response.status_code >= 400:
                # The whole of QuickBooks' complaint, which is usually a named
                # field: without it a 400 is unfixable from a log.
                logs.log(
                    "quickbooks refused the call",
                    method=method,
                    url=url,
                    status=response.status_code,
                    said=response.text[:400],
                    intuit_tid=self.last_intuit_tid,
                )
                raise QuickBooksFailed(
                    with_trace(
                        f"{method} {url} returned {response.status_code}: {response.text[:400]}",
                        self.last_intuit_tid,
                    )
                )
            if accept == "application/pdf":
                return response.content
            return response.json() if response.content else {}
        raise QuickBooksFailed(f"{method} {url} kept failing; I'll try again next run")

    def get(self, url: str, accept: str = "application/json") -> Any:
        return self.request("GET", url, accept=accept)

    def post(self, url: str, json: Any = None) -> Any:
        return self.request("POST", url, json=json)

    def query(self, statement: str) -> list[dict[str, Any]]:
        """A QuickBooks query; returns the rows of whatever entity was asked for."""
        payload = self.get(self.company_url(f"/query?query={quote(statement)}"))
        response = payload.get("QueryResponse", {})
        for key, value in response.items():
            if isinstance(value, list) and key not in ("maxResults", "startPosition"):
                rows: list[dict[str, Any]] = value
                return rows
        return []
