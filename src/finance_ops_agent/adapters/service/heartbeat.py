"""The heartbeat: one GET to a check service after every run and backup that
finished cleanly (healthchecks.io by default).

The check service, not the agent, is what tells Ash and Kevin when the pings
stop, because an agent that has stopped cannot email anyone. A ping that fails
is logged and forgotten: missing one is exactly what the service's grace
period is for, and the agent's work must never wait on it.
"""

import httpx

from finance_ops_agent import logs

TIMEOUT_SECONDS = 10.0


class Heartbeat:
    def __init__(self, url: str, http: httpx.Client | None = None) -> None:
        self.url = url
        self._http = http or httpx.Client(timeout=TIMEOUT_SECONDS)

    def ping(self) -> None:
        if not self.url:
            return
        try:
            response = self._http.get(self.url)
            response.raise_for_status()
        except httpx.HTTPError as error:
            logs.log("heartbeat not sent", error=type(error).__name__)
