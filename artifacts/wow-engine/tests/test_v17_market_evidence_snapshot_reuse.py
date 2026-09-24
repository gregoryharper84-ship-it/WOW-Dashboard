from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

from v17 import market_evidence_scout_bridge as bridge
from v17 import market_evidence_sources as sources


def _failed(provider: str, capability: str, code: str, status: int = 429):
    return sources.MarketEvidenceResult(False, provider, capability, status=status, code=code)


def _event():
    return {
        "id": "sharpapi-packers-falcons",
        "sport_key": "americanfootball_nfl",
        "commence_time": "2026-09-25T00:15:00Z",
        "home_team": "Green Bay Packers",
        "away_team": "Atlanta Falcons",
        "bookmakers": [{"key": "sharpapi_book", "title": "Research Book", "markets": [{"key": "h2h", "outcomes": [{"name": "Green Bay Packers", "price": -110}, {"name": "Atlanta Falcons", "price": -105}]}]}],
        "_wow_market_evidence": {"provider": "SHARPAPI", "prediction_authority": False, "exact_line_authority": False, "research_only": True, "can_execute": False},
    }


def _snapshot(path, *, generated_at: datetime | None = None, overrides: dict | None = None):
    payload = {
        "schema_version": "wow.v17.market_evidence_snapshot.v2",
        "generated_at": (generated_at or datetime.now(timezone.utc)).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "status": "MARKET_EVIDENCE_CAPTURED",
        "events": [_event()],
        "prediction_authority": False,
        "exact_line_authority": False,
        "research_only": True,
        "can_execute": False,
    }
    payload.update(overrides or {})
    path.write_text(json.dumps(payload))
    return payload


def _fail_live(monkeypatch):
    monkeypatch.setattr(bridge.live, "sharpapi_market_evidence", lambda *args, **kwargs: _failed("SHARPAPI", "odds", "SHARPAPI_HTTP_429"))
    monkeypatch.setattr(bridge.live, "rundown_market_evidence", lambda *args, **kwargs: _failed("RUNDOWN", "events", "RUNDOWN_HTTP_403", 403))


def test_fresh_governed_snapshot_is_reused_when_live_providers_fail(monkeypatch, tmp_path):
    snapshot = tmp_path / "market-evidence.json"
    _snapshot(snapshot)
    monkeypatch.setenv("WOW_MARKET_EVIDENCE_SNAPSHOT_PATH", str(snapshot))
    monkeypatch.setattr(sources, "ENABLED", True)
    _fail_live(monkeypatch)
    result = bridge.market_evidence_for_request("/odds-api/v4/sports/americanfootball_nfl/events", None, {}, primary_failure="ODDS_PROVIDER_NON_JSON:HTTP_429")
    assert result.ok is True
    assert result.provider == bridge.TERTIARY_PROVIDER
    assert result.code == "MARKET_EVIDENCE_USED"
    assert len(result.data) == 1
    marker = result.data[0]["_wow_market_evidence"]
    assert marker["snapshot_reused"] is True
    assert marker["prediction_authority"] is False
    assert marker["exact_line_authority"] is False
    assert marker["research_only"] is True
    assert marker["can_execute"] is False


def test_snapshot_with_authority_is_rejected_fail_closed(monkeypatch, tmp_path):
    snapshot = tmp_path / "market-evidence.json"
    _snapshot(snapshot, overrides={"prediction_authority": True})
    monkeypatch.setenv("WOW_MARKET_EVIDENCE_SNAPSHOT_PATH", str(snapshot))
    monkeypatch.setattr(sources, "ENABLED", True)
    _fail_live(monkeypatch)
    result = bridge.market_evidence_for_request("/odds-api/v4/sports/americanfootball_nfl/events", None, {})
    assert result.ok is False
    assert result.code == "MARKET_EVIDENCE_NO_ROWS"
    assert "SNAPSHOT:MARKET_EVIDENCE_SNAPSHOT_AUTHORITY_INVALID" in result.schema_probe["provider_codes"]


def test_stale_snapshot_is_rejected_fail_closed(monkeypatch, tmp_path):
    snapshot = tmp_path / "market-evidence.json"
    _snapshot(snapshot, generated_at=datetime.now(timezone.utc) - timedelta(hours=3))
    monkeypatch.setenv("WOW_MARKET_EVIDENCE_SNAPSHOT_PATH", str(snapshot))
    monkeypatch.setenv("WOW_MARKET_EVIDENCE_SNAPSHOT_MAX_AGE_MINUTES", "30")
    monkeypatch.setattr(sources, "ENABLED", True)
    _fail_live(monkeypatch)
    result = bridge.market_evidence_for_request("/odds-api/v4/sports/americanfootball_nfl/events", None, {})
    assert result.ok is False
    assert result.code == "MARKET_EVIDENCE_NO_ROWS"
    assert "SNAPSHOT:MARKET_EVIDENCE_SNAPSHOT_STALE" in result.schema_probe["provider_codes"]


def test_cached_event_odds_resolve_via_locked_event_context(monkeypatch, tmp_path):
    snapshot = tmp_path / "market-evidence.json"
    _snapshot(snapshot)
    monkeypatch.setenv("WOW_MARKET_EVIDENCE_SNAPSHOT_PATH", str(snapshot))
    monkeypatch.setattr(sources, "ENABLED", True)
    _fail_live(monkeypatch)
    context = {"primary-provider-event-id": {"home_team": "Green Bay Packers", "away_team": "Atlanta Falcons", "commence_time": "2026-09-25T00:15:00Z"}}
    result = bridge.market_evidence_for_request("/odds-api/v4/sports/americanfootball_nfl/events/primary-provider-event-id/odds", None, context, primary_failure="ODDS_PROVIDER_NON_JSON:HTTP_429")
    assert result.ok is True
    assert result.code == "MARKET_EVIDENCE_USED"
    assert result.data["bookmakers"]
    assert all(book.get("source_provider") for book in result.data["bookmakers"])
    marker = result.data["_wow_market_evidence"]
    assert marker["prediction_authority"] is False
    assert marker["exact_line_authority"] is False
    assert marker["can_execute"] is False


def test_snapshot_reuse_never_crosses_sport_identity(monkeypatch, tmp_path):
    snapshot = tmp_path / "market-evidence.json"
    _snapshot(snapshot)
    monkeypatch.setenv("WOW_MARKET_EVIDENCE_SNAPSHOT_PATH", str(snapshot))
    monkeypatch.setattr(sources, "ENABLED", True)
    _fail_live(monkeypatch)
    result = bridge.market_evidence_for_request("/odds-api/v4/sports/baseball_mlb/events", None, {})
    assert result.ok is False
    assert result.code == "MARKET_EVIDENCE_NO_ROWS"
    assert "SNAPSHOT:MARKET_EVIDENCE_SNAPSHOT_NO_ROWS" in result.schema_probe["provider_codes"]


def test_healthy_live_provider_does_not_mix_snapshot(monkeypatch, tmp_path):
    snapshot = tmp_path / "market-evidence.json"
    _snapshot(snapshot)
    monkeypatch.setenv("WOW_MARKET_EVIDENCE_SNAPSHOT_PATH", str(snapshot))
    monkeypatch.setattr(sources, "ENABLED", True)
    live_event = _event()
    live_event["id"] = "live-provider-event"
    monkeypatch.setattr(bridge.live, "sharpapi_market_evidence", lambda *args, **kwargs: sources.MarketEvidenceResult(True, "SHARPAPI", "odds", data=[live_event], status=200, code="MARKET_EVIDENCE_USED"))
    monkeypatch.setattr(bridge.live, "rundown_market_evidence", lambda *args, **kwargs: _failed("RUNDOWN", "events", "RUNDOWN_HTTP_403", 403))
    result = bridge.market_evidence_for_request("/odds-api/v4/sports/americanfootball_nfl/events", None, {})
    assert result.ok is True
    assert [row["id"] for row in result.data] == ["live-provider-event"]
    assert all(not (row.get("_wow_market_evidence") or {}).get("snapshot_reused") for row in result.data)


def test_event_level_authority_conflict_is_rejected_fail_closed(monkeypatch, tmp_path):
    snapshot = tmp_path / "market-evidence.json"
    event = _event()
    event["_wow_market_evidence"]["prediction_authority"] = True
    _snapshot(snapshot, overrides={"events": [event]})
    monkeypatch.setenv("WOW_MARKET_EVIDENCE_SNAPSHOT_PATH", str(snapshot))
    monkeypatch.setattr(sources, "ENABLED", True)
    _fail_live(monkeypatch)
    result = bridge.market_evidence_for_request("/odds-api/v4/sports/americanfootball_nfl/events", None, {})
    assert result.ok is False
    assert result.code == "MARKET_EVIDENCE_NO_ROWS"
    assert "SNAPSHOT:MARKET_EVIDENCE_SNAPSHOT_AUTHORITY_INVALID" in result.schema_probe["provider_codes"]
