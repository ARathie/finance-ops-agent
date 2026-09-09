"""Replay recorded HTTP responses through a real httpx client.

Used by the QuickBooks Online tests. Every recorded response is a JSON file
under tests/fixtures/qbo/, keyed by "METHOD url". A recording carries the
status, headers, and body exactly as the service returned them, with secrets
removed; the tests drive the real adapter code against them, so no network
and no credentials are ever needed.
"""

import json
from collections.abc import Iterable
from typing import Any

import httpx


class Replay:
    """A recorded conversation: each key may hold several responses in order."""

    def __init__(self, script: dict[str, list[dict[str, Any]]]) -> None:
        self.script = {key: list(values) for key, values in script.items()}
        self.calls: list[tuple[str, str]] = []
        self.bodies: list[Any] = []
        self.headers: list[dict[str, str]] = []

    def _key(self, request: httpx.Request) -> str:
        return f"{request.method} {request.url}"

    def handler(self, request: httpx.Request) -> httpx.Response:
        key = self._key(request)
        self.calls.append((request.method, str(request.url)))
        self.headers.append(dict(request.headers))
        if request.content:
            try:
                self.bodies.append(json.loads(request.content))
            except ValueError:
                self.bodies.append(request.content)
        queue = self.script.get(key)
        if not queue:
            raise AssertionError(f"no recorded response for {key}")
        recorded = queue.pop(0) if len(queue) > 1 else queue[0]
        content = recorded.get("content")
        if content is not None:
            body = content.encode() if isinstance(content, str) else bytes(content)
            return httpx.Response(
                recorded["status"], content=body, headers=recorded.get("headers", {})
            )
        return httpx.Response(
            recorded["status"],
            json=recorded.get("json", {}),
            headers=recorded.get("headers", {}),
        )

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))

    def urls(self) -> Iterable[str]:
        return [url for _, url in self.calls]
