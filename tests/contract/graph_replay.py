"""Replay recorded Graph responses through a real httpx client.

Every recorded response is a JSON file under tests/fixtures/graph/, keyed by
"METHOD path". A recording carries the status, headers, and body exactly as
Graph returned them, with secrets removed; the tests below drive the real
adapter code against them, so no network and no credentials are ever needed.

The recorder is `tests/contract/record_graph.py` (run by hand with real
credentials); these files were written from the shapes documented in
docs/integrations/microsoft-365-email.md.
"""

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import httpx

RECORDINGS = Path(__file__).parent.parent / "fixtures" / "graph"


class Replay:
    """A recorded conversation: each key may hold several responses in order."""

    def __init__(self, script: dict[str, list[dict[str, Any]]]) -> None:
        self.script = {key: list(values) for key, values in script.items()}
        self.calls: list[tuple[str, str]] = []
        self.bodies: list[Any] = []
        self.headers: list[dict[str, str]] = []

    @classmethod
    def from_files(cls, *names: str) -> "Replay":
        script: dict[str, list[dict[str, Any]]] = {}
        for name in names:
            recorded = json.loads((RECORDINGS / f"{name}.json").read_text())
            for entry in recorded["exchanges"]:
                script.setdefault(entry["key"], []).append(entry["response"])
        return cls(script)

    def _key(self, request: httpx.Request) -> str:
        url = str(request.url)
        # Recorded keys are stable: the method and the url with the graph host
        # and any tenant-specific mailbox left in, since those are not secret.
        return f"{request.method} {url}"

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
