"""Shared fixtures.

Every test here is offline. Nothing in this suite touches Alpha Vantage or
SEC EDGAR -- the whole point is that the fragile parsing and guard logic can
be exercised without spending any of a 25-request daily budget.
"""

import os
import sys
from typing import Any

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class FakeResponse:
    def __init__(self, payload: dict[str, Any], status: int = 200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict[str, Any]:
        return self._payload


class FakeSession:
    """Returns queued payloads in order and records the params it was given."""

    def __init__(self, payloads: list[dict[str, Any]]):
        self.payloads = list(payloads)
        self.calls: list[dict[str, Any]] = []
        self.headers: dict[str, str] = {}

    def get(self, url, params=None, timeout=None):  # noqa: D102
        self.calls.append(params or {})
        payload = self.payloads.pop(0) if self.payloads else {}
        return FakeResponse(payload)


@pytest.fixture(autouse=True)
def _fast_and_keyed(monkeypatch):
    """Remove the real pacing and backoff, and supply a dummy API key.

    The client deliberately sleeps 1.2s between requests and backs off 5s
    then 15s on a throttle. Correct in production, intolerable in a test
    suite, so both are zeroed here.
    """
    from app import alphavantage as av

    monkeypatch.setattr(av, "_MIN_INTERVAL", 0)
    monkeypatch.setattr(av, "_RETRY_BACKOFF", (0, 0))
    monkeypatch.setattr(av, "_last_call", 0.0)
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", "test-key")
