"""Record real Graph responses for the replay tests. Run by hand, with credentials.

    MS_TENANT_ID=... MS_CLIENT_ID=... MS_CLIENT_SECRET=... \
    FOPS_AGENT_MAILBOX=jay@icon-technologies.com \
    uv run python tests/contract/record_graph.py delta_paging

It writes tests/fixtures/graph/<name>.json in the shape the replay harness
reads. Secrets never reach the file: the Authorization header is not recorded,
and you should read the result before committing it - message bodies and
attachment names from a real mailbox are real client data. Prefer recording
against a mailbox holding only made-up mail.
"""

import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx

from finance_ops_agent.adapters.microsoft365.client import GraphClient, token_from_msal
from finance_ops_agent.adapters.microsoft365.inbox import GraphInbox

OUT = Path(__file__).parent.parent / "fixtures" / "graph"
REDACTED_HEADERS = {"authorization", "set-cookie", "client-request-id"}


class RecordingTransport(httpx.BaseTransport):
    def __init__(self) -> None:
        self.exchanges: list[dict[str, Any]] = []
        self._real = httpx.HTTPTransport()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response = self._real.handle_request(request)
        response.read()
        headers = {
            name: value
            for name, value in response.headers.items()
            if name.lower() not in REDACTED_HEADERS
        }
        entry: dict[str, Any] = {
            "key": f"{request.method} {request.url}",
            "response": {"status": response.status_code, "headers": headers},
        }
        try:
            entry["response"]["json"] = json.loads(response.content)
        except ValueError:
            entry["response"]["content"] = response.content.decode("latin-1")
        self.exchanges.append(entry)
        return response


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    name = sys.argv[1]
    mailbox = os.environ["FOPS_AGENT_MAILBOX"]
    transport = RecordingTransport()
    client = GraphClient(
        mailbox,
        lambda: token_from_msal(
            os.environ["MS_TENANT_ID"],
            os.environ["MS_CLIENT_ID"],
            os.environ["MS_CLIENT_SECRET"],
        ),
        http=httpx.Client(transport=transport, timeout=60.0),
    )
    emails, cursor = GraphInbox(client).new_messages(None)
    print(f"read {len(emails)} message(s); cursor ends {cursor[-24:]}")
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{name}.json"
    path.write_text(json.dumps({"exchanges": transport.exchanges}, indent=2))
    print(f"wrote {path}. Read it before committing: it may hold real client data.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
