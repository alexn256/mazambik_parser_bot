import asyncio

import httpx
import pytest

import sender
from sender import send_message


class FakeResponse:
    def __init__(self, status_code: int, json_data: dict | None = None):
        self.status_code = status_code
        self._json = json_data or {}
        self.text = str(json_data)

    def json(self):
        return self._json


class FakeClient:
    """Stands in for httpx.AsyncClient; pops one scripted response per post()."""

    scripted: list = []
    calls: int = 0

    def __init__(self, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, json=None):
        FakeClient.calls += 1
        item = FakeClient.scripted.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def fake_http(monkeypatch):
    """Patch out the HTTP client and sleeps; returns the recorded sleep list."""
    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    FakeClient.scripted = []
    FakeClient.calls = 0
    monkeypatch.setattr(sender.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(sender.asyncio, "sleep", fake_sleep)
    return sleeps


class TestSendMessage:
    def test_success_first_try(self, fake_http):
        FakeClient.scripted = [FakeResponse(200)]
        assert asyncio.run(send_message("t", 1, "hi")) is True
        assert FakeClient.calls == 1
        assert fake_http == []  # no sleeping on success

    def test_permanent_error_no_retry(self, fake_http):
        # 403 = user blocked the bot: exactly one attempt, immediate False
        FakeClient.scripted = [FakeResponse(403)]
        assert asyncio.run(send_message("t", 1, "hi")) is False
        assert FakeClient.calls == 1

    def test_rate_limit_respects_retry_after(self, fake_http):
        FakeClient.scripted = [
            FakeResponse(429, {"parameters": {"retry_after": 7}}),
            FakeResponse(200),
        ]
        assert asyncio.run(send_message("t", 1, "hi")) is True
        assert 7 in fake_http

    def test_transient_network_error_retried(self, fake_http):
        FakeClient.scripted = [
            httpx.ConnectError("boom"),
            httpx.ConnectError("boom"),
            FakeResponse(200),
        ]
        assert asyncio.run(send_message("t", 1, "hi")) is True
        assert FakeClient.calls == 3
        assert fake_http == [2, 4]  # exponential backoff between attempts

    def test_server_error_exhausts_attempts(self, fake_http):
        FakeClient.scripted = [FakeResponse(500)] * 3
        assert asyncio.run(send_message("t", 1, "hi")) is False
        assert FakeClient.calls == 3
