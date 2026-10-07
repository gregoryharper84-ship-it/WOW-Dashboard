from __future__ import annotations

from v17 import durable_provider_cache as durable
from v17 import market_evidence_native_live as live
from v17 import market_evidence_observability as observability
from v17 import market_evidence_sources as sources
from v17 import rundown_snapshot_cache as memory_cache


def _cached():
    return sources.MarketEvidenceResult(
        True,
        "RUNDOWN",
        "events",
        data=[{
            "id": "cached-event",
            "sport_key": "baseball_mlb",
            "home_team": "Home",
            "away_team": "Away",
            "bookmakers": [],
        }],
        status=200,
        code="MARKET_EVIDENCE_NORMALISED",
        observed_at="2026-10-07T13:00:00Z",
        prediction_authority=False,
        exact_line_authority=False,
        research_only=True,
        request_audit={"cache_origin": "DURABLE_CACHE"},
        can_execute=False,
    )


def test_durable_cache_hit_skips_paid_provider_fetch(monkeypatch):
    memory_cache.reset()
    observability.reset()
    monkeypatch.setattr(
        sources,
        "rundown_sport_id",
        lambda *_a, **_k: sources.MarketEvidenceResult(
            True, "RUNDOWN", "sports", data=3, status=200
        ),
    )
    monkeypatch.setattr(durable, "load_market_evidence", lambda *_a, **_k: _cached())
    monkeypatch.setattr(
        live,
        "_fetch_with_bounded_429_retry",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("paid provider must not be called on durable cache hit")
        ),
    )

    out = live.get_sport_date_odds_snapshot(
        "baseball_mlb",
        "2026-10-07",
        capability="events",
        sport_id=3,
    )

    assert out.ok is True
    assert out.data[0]["id"] == "cached-event"
    assert out.request_audit["cache_origin"] == "DURABLE_CACHE"
    assert out.request_audit["memory_cache_origin"] == "PROVIDER"
    counters = observability.counters()
    assert counters["rundown_durable_cache_hits"] == 1
    assert counters["rundown_provider_calls"] == 0


def test_provider_success_writes_durable_cache_best_effort(monkeypatch):
    memory_cache.reset()
    observability.reset()
    monkeypatch.setattr(durable, "load_market_evidence", lambda *_a, **_k: None)
    stored = []
    monkeypatch.setattr(
        durable,
        "store_market_evidence",
        lambda *a, **k: stored.append((a, k)) or True,
    )
    raw = {
        "events": [{
            "event_id": "123",
            "event_date": "2026-10-07T20:00:00Z",
            "teams": [
                {"team_id": 1, "is_home": True, "name": "Home"},
                {"team_id": 2, "is_home": False, "name": "Away"},
            ],
            "markets": [],
        }]
    }
    monkeypatch.setattr(
        live,
        "_fetch_with_bounded_429_retry",
        lambda *_a, **_k: sources.MarketEvidenceResult(
            True,
            "RUNDOWN",
            "events",
            data=raw,
            status=200,
            code="MARKET_EVIDENCE_FETCH_OK",
            observed_at="2026-10-07T13:00:00Z",
        ),
    )
    # Avoid binding this integration test to provider-v2 payload details; return
    # one normalized event after the provider response passed structural checks.
    monkeypatch.setattr(
        live.payload_contract,
        "classify_rundown_payload",
        lambda _data: type("C", (), {"kind": "ODDS_SNAPSHOT", "is_odds_snapshot": True})(),
    )
    monkeypatch.setattr(
        live.payload_contract,
        "schema_diagnostics",
        lambda *_a, **_k: {},
    )
    monkeypatch.setattr(live, "_candidate_events", lambda _data: [{"event_id": "123"}])
    monkeypatch.setattr(
        live,
        "rundown_v2_event_to_odds_api_v4",
        lambda _raw, *, sport_key: {
            "id": "rundown-123",
            "sport_key": sport_key,
            "home_team": "Home",
            "away_team": "Away",
            "bookmakers": [],
        },
    )

    out = live.get_sport_date_odds_snapshot(
        "baseball_mlb",
        "2026-10-07",
        capability="events",
        sport_id=3,
    )

    assert out.ok is True
    assert len(stored) == 1
    assert stored[0][1]["provider"] == "RUNDOWN"
    assert stored[0][1]["sport_key"] == "baseball_mlb"
    assert observability.counters()["rundown_durable_cache_writes"] == 1
