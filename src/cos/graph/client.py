"""Microsoft Graph client.

A thin client over `httpx` against Graph v1.0, rather than the generated SDK. The system
touches a handful of endpoints, everything is normalised into the internal models at the
boundary, and the SDK's own types would be discarded a line after construction. A thin
client also makes fixture replay trivial: one transport swap, no monkey-patching of
generated code.

No beta endpoints. Graph throttles, and it will choose the worst possible moment, so 429
and 5xx retry with backoff that honours `Retry-After`.
"""

from __future__ import annotations

import random
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx

from cos.graph.auth import GRAPH_BASE, GraphAuth
from cos.logging import get_logger

log = get_logger("graph.client")

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_RETRIES = 4
MAX_BACKOFF_SECONDS = 30.0


class GraphError(RuntimeError):
    """A Graph call that failed in a way retrying will not fix."""

    def __init__(self, status: int, url: str, body: str) -> None:
        super().__init__(f"Graph {status} on {url}: {body[:500]}")
        self.status = status
        self.url = url
        self.body = body


class TokenProvider:
    """Anything that can hand over a bearer token."""

    def token(self) -> str:  # pragma: no cover - protocol shim
        raise NotImplementedError


@dataclass
class StaticToken(TokenProvider):
    """For fixtures and tests. Never used against the live service."""

    value: str = "fixture-token"

    def token(self) -> str:
        return self.value


@dataclass
class GraphClient:
    """Synchronous Graph client with retries and paging.

    `transport` is injectable so tests replay recorded responses. That is the only reason
    this class does not simply construct its own client — and it is reason enough, since
    it is what lets the whole suite run with no network and no credentials.
    """

    auth: TokenProvider | GraphAuth
    transport: httpx.BaseTransport | None = None
    base_url: str = GRAPH_BASE
    timeout: float = 30.0
    sleep: Any = time.sleep
    _client: httpx.Client = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._client = httpx.Client(transport=self.transport, timeout=self.timeout)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> GraphClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _headers(self, extra: Mapping[str, str] | None = None) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.auth.token()}",
            "Accept": "application/json",
        }
        if extra:
            headers.update(extra)
        return headers

    def _backoff(self, attempt: int, response: httpx.Response | None) -> float:
        """Honour Retry-After when Graph sends it; exponential with jitter otherwise.

        Graph's Retry-After is authoritative and ignoring it makes throttling worse
        rather than better.
        """
        if response is not None:
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                try:
                    return min(float(retry_after), MAX_BACKOFF_SECONDS)
                except ValueError:
                    pass
        jitter: float = random.uniform(0, 0.25 * (2**attempt))
        return float(min(2**attempt + jitter, MAX_BACKOFF_SECONDS))

    def request(
        self,
        method: str,
        path_or_url: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Any = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        url = (
            path_or_url
            if path_or_url.startswith("http")
            else f"{self.base_url}/{path_or_url.lstrip('/')}"
        )

        last: httpx.Response | None = None
        for attempt in range(MAX_RETRIES + 1):
            response = self._client.request(
                method, url, params=params, json=json_body, headers=self._headers(headers)
            )
            if response.status_code not in RETRY_STATUSES:
                if response.is_error:
                    raise GraphError(response.status_code, url, response.text)
                return response

            last = response
            if attempt == MAX_RETRIES:
                break
            delay = self._backoff(attempt, response)
            log.warning(
                "graph throttled or unavailable, backing off",
                status=response.status_code,
                attempt=attempt + 1,
                delay_s=round(delay, 2),
                url=url,
            )
            self.sleep(delay)

        assert last is not None
        raise GraphError(last.status_code, url, last.text)

    def get(self, path: str, **kwargs: Any) -> dict[str, Any]:
        payload: dict[str, Any] = self.request("GET", path, **kwargs).json()
        return payload

    def post(self, path: str, json_body: Any, **kwargs: Any) -> dict[str, Any]:
        response = self.request("POST", path, json_body=json_body, **kwargs)
        if not response.content:
            return {}
        payload: dict[str, Any] = response.json()
        return payload

    def paged(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        max_items: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Follow `@odata.nextLink` until exhausted or `max_items` is reached.

        `max_items` exists because an unbounded page-follow against a busy mailbox is how
        a demo run turns into a five-minute wait.
        """
        seen = 0
        url: str | None = path
        next_params: Mapping[str, Any] | None = params

        while url:
            payload = self.get(url, params=next_params)
            for item in payload.get("value", []):
                yield item
                seen += 1
                if max_items is not None and seen >= max_items:
                    return
            url = payload.get("@odata.nextLink")
            # nextLink carries its own query string; re-sending params would duplicate it.
            next_params = None
