import asyncio
from types import SimpleNamespace

from v17_synthetic_self_acceptance import _post_with_transient_gateway_retry


class _Client:
    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.calls = 0

    async def post(self, _url, *, headers, json):
        self.calls += 1
        return SimpleNamespace(status_code=self.statuses.pop(0))


def test_transient_gateway_is_retried_until_application_response(monkeypatch):
    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    client = _Client([502, 503, 200])
    response, attempts = asyncio.run(
        _post_with_transient_gateway_retry(client, "https://example.test", headers={}, json={})
    )
    assert response.status_code == 200
    assert attempts == 3
    assert client.calls == 3


def test_non_gateway_application_failure_is_not_retried(monkeypatch):
    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    client = _Client([409, 200])
    response, attempts = asyncio.run(
        _post_with_transient_gateway_retry(client, "https://example.test", headers={}, json={})
    )
    assert response.status_code == 409
    assert attempts == 1
    assert client.calls == 1


def test_gateway_retry_is_bounded(monkeypatch):
    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    client = _Client([502, 502, 502, 502])
    response, attempts = asyncio.run(
        _post_with_transient_gateway_retry(client, "https://example.test", headers={}, json={})
    )
    assert response.status_code == 502
    assert attempts == 4
    assert client.calls == 4
