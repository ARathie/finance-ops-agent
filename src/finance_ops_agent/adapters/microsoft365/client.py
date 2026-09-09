"""A small Microsoft Graph client: tokens, retries, and the raw calls.

Only the endpoints the agent needs. Throttling follows the contract: 429 and
503 wait for Retry-After and retry, at most three tries per run; a 401 buys
one fresh token; everything else raises. TLS verification is never disabled.
"""

import time
from collections.abc import Callable
from typing import Any

import httpx

GRAPH = "https://graph.microsoft.com/v1.0"
IMMUTABLE_IDS = 'IdType="ImmutableId"'
SCOPES = ["https://graph.microsoft.com/.default"]
MAX_TRIES = 3
MAX_RETRY_AFTER_SECONDS = 120


class MailboxProblem(Exception):
    """The mailbox could not be reached or read (becomes MAILBOX_PROBLEM)."""


class DeltaExpired(Exception):
    """The saved delta link is too old (410 Gone); start again without one."""


def token_from_msal(tenant_id: str, client_id: str, client_secret: str) -> str:
    """Acquire an application token. Imported lazily so tests need no msal."""
    import msal

    app = msal.ConfidentialClientApplication(
        client_id,
        authority=f"https://login.microsoftonline.com/{tenant_id}",
        client_credential=client_secret,
    )
    result = app.acquire_token_for_client(scopes=SCOPES)
    if not isinstance(result, dict) or "access_token" not in result:
        description = ""
        if isinstance(result, dict):
            description = str(result.get("error_description") or result.get("error") or "")
        raise MailboxProblem(f"could not get a token for the mailbox: {description}")
    return str(result["access_token"])


class GraphClient:
    def __init__(
        self,
        mailbox: str,
        get_token: Callable[[], str],
        http: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.mailbox = mailbox
        self._get_token = get_token
        self._http = http or httpx.Client(timeout=60.0)
        self._sleep = sleep
        self._token: str | None = None

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        if self._token is None:
            self._token = self._get_token()
        headers = {"Authorization": f"Bearer {self._token}"}
        # Ids must survive a message being moved between folders, on every call.
        # Graph takes several preferences at once, comma separated, so a
        # caller's own Prefer joins this one rather than replacing it.
        preferences = [IMMUTABLE_IDS]
        for name, value in (extra or {}).items():
            if name.casefold() == "prefer":
                preferences.append(value)
            else:
                headers[name] = value
        headers["Prefer"] = ", ".join(preferences)
        return headers

    def _retry_after(self, response: httpx.Response) -> float:
        try:
            wait = float(response.headers.get("Retry-After", "5"))
        except ValueError:
            wait = 5.0
        return min(max(wait, 0.0), MAX_RETRY_AFTER_SECONDS)

    def request(
        self,
        method: str,
        url: str,
        *,
        json: Any = None,
        headers: dict[str, str] | None = None,
        raw: bool = False,
    ) -> Any:
        """One Graph call, with the documented retry rules."""
        if url.startswith("/"):
            url = GRAPH + url
        refreshed = False
        for attempt in range(1, MAX_TRIES + 1):
            response = self._http.request(method, url, json=json, headers=self._headers(headers))
            if response.status_code == 401 and not refreshed:
                self._token = None  # one fresh token, then give up
                refreshed = True
                continue
            if response.status_code in (429, 503) and attempt < MAX_TRIES:
                self._sleep(self._retry_after(response))
                continue
            if response.status_code == 410:
                raise DeltaExpired(url)
            if response.status_code >= 400:
                raise MailboxProblem(
                    f"{method} {url} returned {response.status_code}: {response.text[:400]}"
                )
            if raw:
                return response.content
            if not response.content:
                return {}
            return response.json()
        raise MailboxProblem(f"{method} {url} kept failing; I'll try again next run")

    def get(self, url: str, headers: dict[str, str] | None = None) -> Any:
        return self.request("GET", url, headers=headers)

    def get_bytes(self, url: str) -> bytes:
        content = self.request("GET", url, raw=True)
        assert isinstance(content, bytes)
        return content

    def post(self, url: str, json: Any = None) -> Any:
        return self.request("POST", url, json=json)

    def patch(self, url: str, json: Any = None) -> Any:
        return self.request("PATCH", url, json=json)

    def put_chunk(self, upload_url: str, content: bytes, headers: dict[str, str]) -> int:
        """One chunk of an upload session. The upload url carries its own
        authorisation, so no token header goes with it."""
        response = self._http.put(upload_url, content=content, headers=headers)
        return response.status_code

    def user_url(self, suffix: str) -> str:
        return f"{GRAPH}/users/{self.mailbox}{suffix}"
