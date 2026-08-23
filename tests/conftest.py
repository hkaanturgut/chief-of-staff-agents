"""Fixture replay.

CI has no tenant, no token, and no network path to Microsoft Graph. Everything replays
from `tests/fixtures/`, keyed by a hash of method, path, and sorted query parameters.

The transport **fails loudly on a cache miss**. That matters more than it looks: a
transport that fell through to the network would make an accidental live call in CI look
like a passing test, and the whole point of Principle VI is that it cannot.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import httpx
import pytest

FIXTURES = Path(__file__).parent / "fixtures"


class FixtureMiss(AssertionError):
    """Raised when a test asks for a request that was never recorded."""


def fixture_key(method: str, url: str) -> str:
    parts = urlsplit(url)
    query = "&".join(f"{k}={v}" for k, v in sorted(parse_qsl(parts.query)))
    raw = f"{method.upper()} {parts.path}?{query}"
    return hashlib.blake2b(raw.encode(), digest_size=8).hexdigest()


class ReplayTransport(httpx.BaseTransport):
    """Serves recorded Graph responses. Never reaches the network."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or FIXTURES
        self.requests: list[str] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(f"{request.method} {url}")
        path = self.directory / f"{fixture_key(request.method, url)}.json"
        if not path.exists():
            raise FixtureMiss(
                f"no recorded response for {request.method} {url}\n"
                f"expected fixture: {path.name}\n"
                "Record it with `uv run python scripts/record_fixtures.py`, or the test "
                "is asking for something the pipeline should not be requesting."
            )
        payload = json.loads(path.read_text())
        return httpx.Response(
            status_code=payload.get("status", 200),
            json=payload.get("body", {}),
            request=request,
        )


class StubTransport(httpx.BaseTransport):
    """Serves responses handed to it inline. For unit tests that own their data."""

    def __init__(self, responses: list[tuple[int, Any]] | None = None) -> None:
        self.responses = list(responses or [])
        self.requests: list[httpx.Request] = []

    def add(self, body: Any, status: int = 200) -> StubTransport:
        self.responses.append((status, body))
        return self

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self.responses:
            raise FixtureMiss(f"unexpected request: {request.method} {request.url}")
        status, body = self.responses.pop(0)
        return httpx.Response(status_code=status, json=body, request=request)


@pytest.fixture
def replay() -> ReplayTransport:
    return ReplayTransport()


@pytest.fixture
def stub() -> StubTransport:
    return StubTransport()
