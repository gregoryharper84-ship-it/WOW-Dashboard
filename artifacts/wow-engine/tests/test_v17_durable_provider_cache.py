from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from v17 import durable_provider_cache as cache
from v17.market_evidence_sources import MarketEvidenceResult


class _Query:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.upserted = None

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def gt(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def upsert(self, row, **_kwargs):
        self.upserted = row
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows)


class _DB:
    def __init__(self, query):
        self.query = query

    def table(self, name):
        assert name == cache.TABLE
        return self.query


def _result():
    return MarketEvidenceResult(
        True,
        "RUNDOWN",
        "events",
        data=[{"id": "event-1"}],
        status=200,
        code="MARKET_EVIDENCE_NORMALISED",
        observed_at="2026-10-07T13:00:00+00:00",
        prediction_authority=False,
        exact_line_authority=False,
        research_only=True,
        request_audit={"cache_origin": "PROVIDER"},
        can_execute=False,
    )


def test_successful_non_authoritative_result_is_cacheable(monkeypatch):
    monkeypatch.setenv("WOW_V17_DURABLE_PROVIDER_CACHE_ENABLED", "true")
    monkeypatch.setenv("WOW_V17_DURABLE_PROVIDER_CACHE_TTL_SECONDS", "120")
    query = _Query()
    ok = cache.store_market_evidence(
        ("RUNDOWN", "events", "baseball_mlb"),
        _result(),
        provider="RUNDOWN",
        capability="events",
        sport_key="baseball_mlb",
        slate_date="2026-10-07",
        client=_DB(query),
        now=datetime(2026, 10, 7, 13, 0, tzinfo=timezone.utc),
    )
    assert ok is True
    assert query.upserted["can_execute"] is False
    assert query.upserted["response_meta"]["prediction_authority"] is False
    assert query.upserted["expires_at"] == "2026-10-07T13:02:00+00:00"


def test_failure_is_never_cached(monkeypatch):
    monkeypatch.setenv("WOW_V17_DURABLE_PROVIDER_CACHE_ENABLED", "true")
    failed = MarketEvidenceResult(False, "RUNDOWN", "events", code="RUNDOWN_HTTP_429")
    query = _Query()
    assert cache.store_market_evidence(
        ("x",), failed,
        provider="RUNDOWN", capability="events",
        sport_key="baseball_mlb", slate_date="2026-10-07",
        client=_DB(query),
    ) is False
    assert query.upserted is None


def test_fresh_cache_load_preserves_evidence_only_authority(monkeypatch):
    monkeypatch.setenv("WOW_V17_DURABLE_PROVIDER_CACHE_ENABLED", "true")
    meta = {
        "ok": True,
        "provider": "RUNDOWN",
        "capability": "events",
        "status": 200,
        "code": "MARKET_EVIDENCE_NORMALISED",
        "observed_at": "2026-10-07T13:00:00+00:00",
        "prediction_authority": False,
        "exact_line_authority": False,
        "research_only": True,
        "can_execute": False,
        "request_audit": {"cache_origin": "PROVIDER"},
    }
    query = _Query([{
        "payload": [{"id": "event-1"}],
        "response_meta": meta,
        "expires_at": "2026-10-07T13:02:00+00:00",
    }])
    out = cache.load_market_evidence(
        ("RUNDOWN", "events", "baseball_mlb"),
        client=_DB(query),
        now=datetime(2026, 10, 7, 13, 1, tzinfo=timezone.utc),
    )
    assert out.ok is True
    assert out.data == [{"id": "event-1"}]
    assert out.prediction_authority is False
    assert out.exact_line_authority is False
    assert out.research_only is True
    assert out.request_audit["cache_origin"] == "DURABLE_CACHE"
    assert out.can_execute is False


def test_authority_drift_is_rejected_on_read(monkeypatch):
    monkeypatch.setenv("WOW_V17_DURABLE_PROVIDER_CACHE_ENABLED", "true")
    meta = {
        "ok": True,
        "provider": "RUNDOWN",
        "capability": "events",
        "prediction_authority": True,
        "exact_line_authority": False,
        "research_only": True,
        "can_execute": False,
    }
    query = _Query([{"payload": [], "response_meta": meta, "expires_at": "2099-01-01T00:00:00Z"}])
    assert cache.load_market_evidence(("x",), client=_DB(query)) is None
