from __future__ import annotations

import json
from urllib.error import HTTPError

from v17.fallback_provider_health import fallback_provider_allowed, probe_odds_api_health


class _Response:
    status = 200

    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _clear(monkeypatch):
    for name in ("ODDS_API_KEY_100K", "ODDS_API_PAID_KEY", "ODDS_API_FREE_KEY", "ODDS_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def test_unconfigured_odds_api_is_not_attempted_or_fallback_eligible(monkeypatch):
    _clear(monkeypatch)

    def opener(*args, **kwargs):
        raise AssertionError("provider must not be called without a credential")

    health = probe_odds_api_health(opener=opener)
    assert health["status"] == "CREDENTIAL_UNCONFIGURED"
    assert health["fallback_eligible"] is False
    assert fallback_provider_allowed(health) is False


def test_deactivated_odds_api_key_is_typed_auth_failed(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("ODDS_API_PAID_KEY", "not-a-real-key")

    def opener(request, timeout=None):
        raise HTTPError(request.full_url, 401, "unauthorized", {}, None)

    health = probe_odds_api_health(opener=opener)
    assert health["status"] == "AUTH_FAILED"
    assert health["credential_source"] == "ODDS_API_PAID_KEY"
    assert health["fallback_eligible"] is False
    assert health["affects_model_capability"] is False
    assert fallback_provider_allowed(health) is False


def test_health_probe_uses_same_paid_then_100k_precedence_as_real_odds_service(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("ODDS_API_PAID_KEY", "paid-key")
    monkeypatch.setenv("ODDS_API_KEY_100K", "high-key")
    seen = {}

    def opener(request, timeout=None):
        seen["url"] = request.full_url
        return _Response([{"key": "baseball_mlb", "active": True}])

    health = probe_odds_api_health(opener=opener)
    assert health["status"] == "PASS"
    assert health["credential_source"] == "ODDS_API_PAID_KEY"
    assert "paid-key" in seen["url"]
    assert "high-key" not in seen["url"]


def test_live_odds_api_catalog_auth_makes_provider_fallback_eligible(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("ODDS_API_KEY_100K", "not-a-real-key")
    seen = {}

    def opener(request, timeout=None):
        seen["url"] = request.full_url
        return _Response([{"key": "baseball_mlb", "active": True}])

    health = probe_odds_api_health(opener=opener)
    assert health["status"] == "PASS"
    assert health["credential_source"] == "ODDS_API_KEY_100K"
    assert health["fallback_eligible"] is True
    assert fallback_provider_allowed(health) is True
    assert "not-a-real-key" in seen["url"]
    assert health["secret_values_exposed"] is False


def test_rate_limited_provider_is_not_fallback_eligible(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("ODDS_API_FREE_KEY", "not-a-real-key")

    def opener(request, timeout=None):
        raise HTTPError(request.full_url, 429, "limited", {}, None)

    health = probe_odds_api_health(opener=opener)
    assert health["status"] == "RATE_LIMITED"
    assert health["fallback_eligible"] is False
