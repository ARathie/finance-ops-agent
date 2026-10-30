"""The heartbeat ping: one GET, and a failure never stops the agent."""

import httpx

from finance_ops_agent.adapters.service.heartbeat import Heartbeat

URL = "https://hc-ping.example/abc"


def test_it_gets_the_url() -> None:
    seen: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200)

    Heartbeat(URL, httpx.Client(transport=httpx.MockTransport(answer))).ping()
    assert seen == [URL]


def test_a_failed_ping_is_swallowed() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    Heartbeat(URL, httpx.Client(transport=httpx.MockTransport(refuse))).ping()

    def error(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    Heartbeat(URL, httpx.Client(transport=httpx.MockTransport(error))).ping()


def test_no_url_means_no_ping() -> None:
    def never(request: httpx.Request) -> httpx.Response:
        raise AssertionError("pinged with no url")

    Heartbeat("", httpx.Client(transport=httpx.MockTransport(never))).ping()
