"""Reproduce documented SharpAPI main-market evidence acceptance without provider calls.

Issue #1374: unfiltered first-page rows and unrecognised documented
market_type aliases are distinct; the accepted request must be ONE filtered
prematch page and never infer prediction authority from provider odds.
"""
from __future__ import annotations

import pytest

from v17 import market_evidence_native_live as live
from v17 import market_evidence_snapshot as snapshot
from v17 import market_evidence_sources as sources
from v17 import free_core_source_policy as source_policy


@pytest.mark.parametrize(("provider_type", "canonical"), [
    ("moneyline", "h2h"),
    ("point_spread", "spreads"),
    ("run_line", "spreads"),
    ("puck_line", "spreads"),
    ("total_points", "totals"),
    ("total_goals", "totals"),
    ("total_runs", "totals"),
])
def test_documented_sharpapi_full_game_main_market_aliases(provider_type, canonical):
    assert sources.canonical_market_key(provider_type) == canonical


@pytest.mark.parametrize("unsafe", [
    "anytime_goal_scorer", "player_goals", "season_winner",
    "1st_quarter_player_points", "unknown_unverified_market",
])
def test_unverified_markets_are_not_promoted_as_full_game_mainlines(unsafe):
    assert sources.canonical_market_key(unsafe) not in sources.CANONICAL_MARKET_KEYS


def _documented_row(market_type="moneyline", *, line=None, selection="Los Angeles Kings"):
    return {
        "event_id": "sharp-verified-game-1",
        "home_team": "Los Angeles Kings",
        "away_team": "Vegas Golden Knights",
        "event_start_time": "2026-11-27T01:00:00Z",
        "market_type": market_type,
        "selection": selection,
        "selection_type": "home" if market_type == "moneyline" else "over",
        "odds_american": -110,
        "line": line,
        "sportsbook": "draftkings",
        "timestamp": "2026-11-25T15:00:00Z",
        "is_player_prop": False,
        "is_live": False,
    }


def test_one_pre_match_mainline_acceptance_call_and_canonical_translation(monkeypatch):
    monkeypatch.setattr(source_policy, "free_core_enabled", lambda: False)
    monkeypatch.setattr(snapshot, "_provider_health_map", lambda: {
        "RUNDOWN": {"provider": "RUNDOWN", "credential_configured": False},
        "SHARPAPI": {"provider": "SHARPAPI", "credential_configured": True},
    })
    monkeypatch.setenv("WOW_MARKET_EVIDENCE_429_RETRIES", "0")
    observed = []

    def fake_fetch(provider, capability, *, params=None, opener=None, **kwargs):
        observed.append((provider, capability, dict(params or {})))
        return sources.MarketEvidenceResult(
            True, "SHARPAPI", "odds", status=200,
            data={"data": [
                _documented_row("moneyline"),
                _documented_row("point_spread", line=-1.5),
                _documented_row("total_goals", line=5.5, selection="Over"),
            ]},
        )

    monkeypatch.setattr(sources, "fetch", fake_fetch)
    acceptance = snapshot.collect_acceptance("icehockey_nhl", date="2026-11-25")
    assert len(observed) == 1, "A repaired adapter must not increase provider calls"
    provider, capability, params = observed[0]
    assert (provider, capability) == ("SHARPAPI", "odds")
    assert params["league"] == "NHL"
    assert params["market"] == "main"
    assert params["is_live"] == "false"
    assert params["is_main_line"] == "true"
    assert acceptance["captured_providers"] == ["SHARPAPI"]
    assert acceptance["captured_rows"] >= 1
    assert acceptance["ready_for_market_evidence"] is True
    assert acceptance["can_execute"] is False
    assert acceptance["prediction_authority"] is False
    assert acceptance["affects_fitted_model_availability"] is False


def test_unknown_provider_market_stays_typed_hold(monkeypatch):
    monkeypatch.setattr(source_policy, "free_core_enabled", lambda: False)

    def fake_fetch(provider, capability, *, params=None, opener=None, **kwargs):
        return sources.MarketEvidenceResult(
            True, "SHARPAPI", "odds", status=200,
            data={"data": [_documented_row("unverified_market")]},
        )

    monkeypatch.setattr(sources, "fetch", fake_fetch)
    result = live.sharpapi_market_evidence("icehockey_nhl", main_only=True)
    assert not result.ok
    assert result.code == "SHARPAPI_SCHEMA_UNRECOGNISED"
    assert result.can_execute is False


def test_free_core_does_not_invoke_provider_even_with_mainline_filter(monkeypatch):
    monkeypatch.setattr(source_policy, "free_core_enabled", lambda: True)
    monkeypatch.setattr(sources, "fetch", lambda *_a, **_k: pytest.fail("provider call forbidden"))
    result = live.sharpapi_market_evidence("icehockey_nhl", main_only=True)
    assert result.ok is False
    assert result.can_execute is False
    assert result.request_audit["paid_provider_network_attempted"] is False
