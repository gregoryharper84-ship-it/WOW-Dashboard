"""Governance and plumbing regression tests for research-only market evidence.

These cover the failure classes recorded in
``.agents/memory/acquisition-routing-patch.md`` — field propagation, join-key
collision and non-canonical key leakage — plus the CLAUDE.md invariants that
matter most here: a market feed is evidence, it fails closed, and it can never
become probability or execution authority.
"""
from __future__ import annotations

import io
import json
from urllib.error import HTTPError

import pytest

from v17 import market_evidence_scout_bridge as bridge
from v17 import market_evidence_snapshot as snapshot
from v17 import market_evidence_sources as sources
from v17.nightly_multiscout import bookmaker_rows

PROBABILITY_KEYS = {
    "probability", "p", "model_probability", "calibrated_lower_bound",
    "edge", "ev", "stake", "approval", "hit", "settlement",
}


class _Response:
    def __init__(self, payload, status=200):
        self._body = json.dumps(payload).encode("utf-8")
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _opener(payload, status=200):
    def open_it(request, timeout=None):
        return _Response(payload, status)
    return open_it


def _raising_opener(exc):
    def open_it(request, timeout=None):
        raise exc
    return open_it


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in list(dict(__import__("os").environ)):
        if name.startswith(("WOW_RUNDOWN", "WOW_SHARPAPI", "RUNDOWN_", "SHARPAPI_", "THERUNDOWN_")):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(sources, "ENABLED", True)
    monkeypatch.setenv("RUNDOWN_API_KEY", "test-rundown-key")
    monkeypatch.setenv("SHARPAPI_API_KEY", "test-sharp-key")
    sources.reset_sport_index()
    yield
    sources.reset_sport_index()


def _rundown_events_payload():
    return {
        "events": [{
            "event_id": "evt-1",
            "event_date": "2026-09-14T23:05:00Z",
            "teams_normalized": [
                {"name": "Chicago Cubs", "is_home": True, "is_away": False},
                {"name": "Milwaukee Brewers", "is_home": False, "is_away": True},
            ],
            "lines": {
                "3": {
                    "affiliate": {"affiliate_id": 3, "affiliate_name": "Pinnacle"},
                    "date_updated": "2026-09-14T18:00:00Z",
                    "moneyline": {"moneyline_home": -135, "moneyline_away": 115},
                    "spread": {"point_spread_home": -1.5, "point_spread_away": 1.5,
                               "point_spread_home_money": 140, "point_spread_away_money": -165},
                    "total": {"total_over": 8.5, "total_under": 8.5,
                              "total_over_money": -105, "total_under_money": -115},
                },
            },
        }],
    }


# --------------------------------------------------------------------------
# Governance ceiling
# --------------------------------------------------------------------------

def test_every_emitted_event_is_research_only_and_non_authoritative():
    result = sources.normalize_market_payload(
        _rundown_events_payload(), provider="RUNDOWN", capability="events",
        sport_key="baseball_mlb", primary_failure="ODDS_API_UPSTREAM_ERROR:HTTP_502",
    )
    assert result.ok
    for event in result.data:
        for marker_key in ("_wow_secondary_source", "_wow_market_evidence"):
            marker = event[marker_key]
            assert marker["provider"] == "RUNDOWN_MARKET_EVIDENCE"
            assert marker["source_class"] == "SPORTSBOOK_FEED"
            assert marker["prediction_authority"] is False
            assert marker["exact_line_authority"] is False
            assert marker["research_only"] is True
            assert marker["can_execute"] is False
            assert marker["primary_source_failure"] == "ODDS_API_UPSTREAM_ERROR:HTTP_502"


def test_market_evidence_never_emits_a_probability_or_stake_field():
    result = sources.normalize_market_payload(
        _rundown_events_payload(), provider="RUNDOWN", capability="events", sport_key="baseball_mlb",
    )
    blob = json.dumps(result.data)
    payload_keys = set()

    def walk(node):
        if isinstance(node, dict):
            payload_keys.update(str(k).lower() for k in node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(result.data)
    assert not (payload_keys & PROBABILITY_KEYS), payload_keys & PROBABILITY_KEYS
    assert "can_execute" in blob


def test_result_defaults_are_fail_closed():
    result = sources.MarketEvidenceResult(True, "RUNDOWN", "events")
    assert result.prediction_authority is False
    assert result.exact_line_authority is False
    assert result.research_only is True
    assert result.can_execute is False


# --------------------------------------------------------------------------
# Normalisation
# --------------------------------------------------------------------------

def test_rundown_lines_translate_into_canonical_odds_api_v4_shape():
    event = sources.rundown_event_to_odds_api_v4(
        _rundown_events_payload()["events"][0], sport_key="baseball_mlb",
    )
    assert event["id"] == "rundown-evt-1"
    assert event["home_team"] == "Chicago Cubs"
    assert event["away_team"] == "Milwaukee Brewers"
    book = event["bookmakers"][0]
    assert book["title"] == "Pinnacle"
    assert [m["key"] for m in book["markets"]] == ["h2h", "spreads", "totals"]
    totals = book["markets"][2]["outcomes"]
    assert {o["name"] for o in totals} == {"Over", "Under"}
    assert all(o["point"] == 8.5 for o in totals)


def test_only_canonical_market_keys_leave_the_module():
    payload = {"bookmakers": [{
        "key": "book_a", "title": "Book A",
        "markets": [
            {"key": "moneyline", "outcomes": [{"name": "A", "price": -110}, {"name": "B", "price": -110}]},
            {"key": "some_unmapped_provider_market", "outcomes": [{"name": "A", "price": 100}]},
        ],
    }], "id": "e1"}
    event = sources.coerce_odds_api_v4_event(payload)
    assert [m["key"] for m in event["bookmakers"][0]["markets"]] == ["h2h"]
    assert all(m["key"] in sources.CANONICAL_MARKET_KEYS for m in event["bookmakers"][0]["markets"])


def test_canonical_market_key_mapping_is_case_and_separator_insensitive():
    for raw in ("Money Line", "MONEYLINE", "money-line", "ml"):
        assert sources.canonical_market_key(raw) == "h2h"
    for raw in ("Point Spread", "handicap", "SPREADS"):
        assert sources.canonical_market_key(raw) == "spreads"
    assert sources.canonical_market_key("player_points") == "player_points"
    assert sources.canonical_market_key("pitcher_strikeouts") == "pitcher_strikeouts"
    assert sources.canonical_market_key("unknown_market") is None
    assert sources.canonical_market_key(None) is None


def test_unrecognised_schema_fails_closed_with_a_value_free_probe():
    payload = {"result": {"weird": [{"mystery_field": 1, "price_cents": 275}]}}
    result = sources.normalize_market_payload(payload, provider="SHARPAPI", capability="odds")
    assert result.ok is False
    assert result.code == "SHARPAPI_SCHEMA_UNRECOGNISED"
    assert result.data is None
    probe_text = json.dumps(result.schema_probe)
    assert "mystery_field" in probe_text
    assert "275" not in probe_text


def test_structural_probe_reports_shape_without_values():
    probe = sources.structural_probe({"events": [{"event_id": "secret-id", "price": -137}]})
    text = json.dumps(probe)
    assert "event_id" in text and "price" in text
    assert "secret-id" not in text and "-137" not in text


def test_partial_rows_are_dropped_rather_than_filled_in():
    payload = {"events": [{
        "event_id": "evt-2",
        "event_date": "2026-09-14T23:05:00Z",
        "teams_normalized": [{"name": "Only Home", "is_home": True}],
        "lines": {"3": {"affiliate": {"affiliate_name": "Pinnacle"},
                        "moneyline": {"moneyline_home": -135}}},
    }]}
    result = sources.normalize_market_payload(payload, provider="RUNDOWN", capability="events")
    assert result.ok is False
    assert result.code == "RUNDOWN_SCHEMA_UNRECOGNISED"


# --------------------------------------------------------------------------
# Transport fail-closed behaviour
# --------------------------------------------------------------------------

def test_disabled_flag_blocks_every_outbound_call(monkeypatch):
    monkeypatch.setattr(sources, "ENABLED", False)
    result = sources.fetch("RUNDOWN", "sports", opener=_raising_opener(AssertionError("must not call")))
    assert result.code == "MARKET_EVIDENCE_DISABLED"


def test_missing_credential_fails_closed_without_a_call(monkeypatch):
    monkeypatch.delenv("RUNDOWN_API_KEY", raising=False)
    result = sources.fetch("RUNDOWN", "sports", opener=_raising_opener(AssertionError("must not call")))
    assert result.code == "MARKET_EVIDENCE_CREDENTIAL_UNCONFIGURED"


@pytest.mark.parametrize("status", [401, 403, 429, 500, 502])
def test_provider_rejections_degrade_to_typed_blockers(status):
    opener = _raising_opener(HTTPError("https://example.test", status, "err", {}, io.BytesIO(b"")))
    result = sources.fetch("SHARPAPI", "odds", opener=opener)
    assert result.ok is False
    assert result.code == f"SHARPAPI_HTTP_{status}"
    assert result.status == status
    assert result.data is None


def test_malformed_body_fails_closed():
    class Bad(_Response):
        def read(self):
            return b"<html>not json</html>"

    result = sources.fetch("SHARPAPI", "odds", opener=lambda request, timeout=None: Bad({}))
    assert result.code == "SHARPAPI_INVALID_JSON"


def test_endpoint_diagnostics_never_echo_the_credential():
    opener = _raising_opener(HTTPError("https://example.test", 401, "err", {}, io.BytesIO(b"")))
    result = sources.rundown_market_evidence("baseball_mlb", "2026-09-14", opener=opener)
    assert "test-rundown-key" not in json.dumps(
        {"code": result.code, "endpoint": result.endpoint, "probe": result.schema_probe}
    )


def test_unconfigured_endpoint_is_not_invented(monkeypatch):
    result = sources.fetch("RUNDOWN", "no_such_capability", opener=_raising_opener(AssertionError("must not call")))
    assert result.code == "MARKET_EVIDENCE_ENDPOINT_UNCONFIGURED"


def test_unknown_provider_is_rejected():
    assert sources.fetch("NOT_A_PROVIDER", "odds").code == "MARKET_EVIDENCE_PROVIDER_UNKNOWN"


# --------------------------------------------------------------------------
# Sport identity is resolved, never guessed
# --------------------------------------------------------------------------

def test_sport_id_is_resolved_from_the_provider_index():
    opener = _opener({"sports": [{"sport_id": 3, "sport_name": "MLB"}, {"sport_id": 2, "sport_name": "NFL"}]})
    assert sources.rundown_sport_id("baseball_mlb", opener=opener).data == "3"
    assert sources.rundown_sport_id("americanfootball_nfl", opener=opener).data == "2"


def test_unmapped_sport_fails_closed_rather_than_defaulting():
    opener = _opener({"sports": [{"sport_id": 3, "sport_name": "MLB"}]})
    result = sources.rundown_sport_id("basketball_nba", opener=opener)
    assert result.ok is False
    assert result.code == "MARKET_EVIDENCE_UNSUPPORTED_SPORT"


def test_pinned_sport_id_wins_without_an_index_call(monkeypatch):
    monkeypatch.setenv("WOW_RUNDOWN_SPORT_ID_BASEBALL_MLB", "3")
    result = sources.rundown_sport_id("baseball_mlb", opener=_raising_opener(AssertionError("must not call")))
    assert result.data == "3"
    assert result.code == "MARKET_EVIDENCE_SPORT_ID_PINNED"


def test_sport_index_failure_blocks_the_capability_call():
    opener = _raising_opener(HTTPError("https://example.test", 403, "err", {}, io.BytesIO(b"")))
    result = sources.rundown_market_evidence("baseball_mlb", "2026-09-14", opener=opener)
    assert result.ok is False
    assert result.code == "RUNDOWN_HTTP_403"


# --------------------------------------------------------------------------
# Downstream plumbing: row completeness and join safety
# --------------------------------------------------------------------------

def test_normalised_events_produce_complete_scout_bookmaker_rows():
    result = sources.normalize_market_payload(
        _rundown_events_payload(), provider="RUNDOWN", capability="events", sport_key="baseball_mlb",
    )
    rows = bookmaker_rows(result.data[0])
    assert rows
    for row in rows:
        assert row["bookmaker"]
        assert row["bookmaker_title"] == "Pinnacle"
        assert row["market_key"] in sources.CANONICAL_MARKET_KEYS
        assert row["outcome_name"]
        assert row["price"] is not None
    assert {row["market_key"] for row in rows} == {"h2h", "spreads", "totals"}


def test_same_book_from_two_providers_is_not_collapsed():
    shared = {
        "id": "e1", "home_team": "Cubs", "away_team": "Brewers",
        "bookmakers": [{"key": "pinnacle", "title": "Pinnacle", "markets": [
            {"key": "h2h", "outcomes": [{"name": "Cubs", "price": -135}, {"name": "Brewers", "price": 115}]}]}],
    }
    a = {**shared, "_wow_market_evidence": {"provider": "RUNDOWN_MARKET_EVIDENCE"}}
    b = {**shared, "_wow_market_evidence": {"provider": "SHARPAPI_MARKET_EVIDENCE"}}
    merged = bridge._merge_books([a, b])
    keys = [book["key"] for book in merged["bookmakers"]]
    assert len(keys) == len(set(keys)) == 2
    assert {book["source_provider"] for book in merged["bookmakers"]} == {
        "RUNDOWN_MARKET_EVIDENCE", "SHARPAPI_MARKET_EVIDENCE",
    }


def test_bridge_is_inert_while_the_feature_is_disabled(monkeypatch):
    monkeypatch.setattr(sources, "ENABLED", False)
    result = bridge.market_evidence_for_request("/odds-api/v4/sports/baseball_mlb/events", None, {})
    assert result.ok is False
    assert result.code == "MARKET_EVIDENCE_DISABLED"


def test_bridge_rejects_a_request_path_it_does_not_serve():
    result = bridge.market_evidence_for_request("/odds-api/v4/sports", None, {})
    assert result.code == "MARKET_EVIDENCE_UNSUPPORTED_REQUEST"


# --------------------------------------------------------------------------
# Daily snapshot reconciliation
# --------------------------------------------------------------------------

def test_snapshot_covers_today_and_the_next_slate():
    from datetime import datetime, timezone
    dates = snapshot.snapshot_dates(datetime(2026, 9, 13, 23, 30, tzinfo=timezone.utc))
    assert dates == ["2026-09-13", "2026-09-14"]


def test_snapshot_reconciles_and_reports_unavailable_without_fabricating(monkeypatch):
    monkeypatch.setattr(sources, "ENABLED", False)
    payload = snapshot.collect(["baseball_mlb"])
    recon = payload["reconciliation"]
    assert recon["balanced"] is True
    assert recon["lanes_requested"] == recon["lanes_captured"] + recon["lanes_blocked"]
    assert recon["lanes_captured"] == 0
    assert payload["status"] == sources.MARKET_DATA_UNOBTAINABLE
    assert payload["events"] == []
    assert payload["can_execute"] is False
    assert all(lane["reason_code"] == "MARKET_EVIDENCE_DISABLED" for lane in payload["lanes"])


def test_provider_health_declares_the_research_ceiling():
    health = sources.provider_health()
    assert health["source_class"] == "SPORTSBOOK_FEED"
    assert health["max_age_minutes"] == 15
    assert health["prediction_authority"] is False
    assert health["exact_line_authority"] is False
    assert health["can_execute"] is False
    assert {p["provider"] for p in health["providers"]} == {"SHARPAPI", "RUNDOWN"}


def test_market_evidence_binds_to_the_governed_sportsbook_feed_policy_rule():
    from v17.scout_source_policy import source_rule

    rule = source_rule(sources.SOURCE_CLASS)
    assert rule.source_class == "SPORTSBOOK_FEED"
    assert rule.trust_tier == sources.TRUST_TIER
    assert rule.max_age_minutes == sources.MAX_AGE_MINUTES
    marker = sources.provider_marker("RUNDOWN")
    assert marker["source_class"] == rule.source_class
    assert marker["trust_tier"] == rule.trust_tier
    assert marker["max_age_minutes"] == rule.max_age_minutes


def test_stale_market_evidence_is_not_research_usable():
    from v17.scout_source_policy import evidence_quality

    fresh = evidence_quality(sources.SOURCE_CLASS, age_minutes=5, confirmed=False)
    stale = evidence_quality(sources.SOURCE_CLASS, age_minutes=90, confirmed=False)
    assert fresh["research_usable"] is True
    assert stale["research_usable"] is False
    assert fresh["prediction_authority"] is False and stale["prediction_authority"] is False
