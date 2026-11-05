"""NBA/NHL research evidence must not modify the certified winner or NFL path."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from v17.llp_nba_nhl_deep_research_audit import (
    FACTOR_FAMILIES,
    inspect_nba_nhl_deep_research,
)
from v17.llp_team_intelligence_bundle import build_llp_team_intelligence_bundle
from v17.llp_team_intelligence_readback import project_llp_intelligence_page

START = datetime(2026, 11, 12, 23, 0, tzinfo=timezone.utc)
MODEL_AT = START - timedelta(minutes=5)
SOURCE_AT = START - timedelta(minutes=7)
EVIDENCE_AT = START - timedelta(minutes=6)


def _event(sport: str) -> dict:
    return {
        "sport": sport,
        "official_event_id": f"{sport.lower()}-test-123",
        "home_team": "BOS",
        "away_team": "NYR" if sport == "NHL" else "LAL",
        "event_start_time_utc": START.isoformat(),
    }


def _winner(sport: str) -> dict:
    return {
        **_event(sport),
        "market_family": "OUTRIGHT_WINNER",
        "side": "BOS",
        "source_receipt_id": "frozen-winner-model-receipt",
        "source_snapshot_id": "frozen-source-snapshot",
        "probability_publishable": True,
        "rank_eligible": True,
        "terminal_authority": "V17_TERMINAL_REDUCER",
        "controlling_engine_identity": "LLP_TEAM_BETTING_ENGINE",
        "model_class": "CERTIFIED_FITTED",
        "model_version": "synthetic-test-version",
        "calibration_version": "synthetic-test-calibration",
        "model_timestamp": MODEL_AT.isoformat(),
        "source_snapshot_timestamp": SOURCE_AT.isoformat(),
        "calibrated_probability": .63,
        "calibrated_probability_lower_bound": .57,
        "calibrated_probability_upper_bound": .69,
        "can_execute": False,
    }


def _context(sport: str) -> dict:
    first = FACTOR_FAMILIES[sport][0]
    return {
        "sport": sport,
        "official_event_id": _event(sport)["official_event_id"],
        "source_receipt_id": "frozen-winner-model-receipt",
        "source_snapshot_id": "frozen-source-snapshot",
        "model_version": "synthetic-test-version",
        "observed_at": EVIDENCE_AT.isoformat(),
        "factors": {
            first: {
                "status": "DOCUMENTED",
                "source_receipt_id": "raw-process-source-1",
                "observed_at": EVIDENCE_AT.isoformat(),
            },
        },
    }


@pytest.mark.parametrize("sport", ["NBA", "NHL"])
def test_missing_context_is_typed_and_does_not_invent_uvi(sport):
    out = build_llp_team_intelligence_bundle(
        event=_event(sport), receipts=[_winner(sport)]
    )
    audit = out["deep_research_audit"]
    assert audit["status"] == "CONTEXT_NOT_SUPPLIED"
    assert len(audit["factor_families_missing"]) == 10
    assert audit["upset_volatility_index"] is None
    assert audit["forecast_fragility"] is None
    assert audit["independently_certified"] is False
    assert audit["can_execute"] is False
    assert out["markets"][0]["calibrated_probability"] == .63
    assert out["probabilities_blended"] is False


@pytest.mark.parametrize("sport,flag", [
    ("NBA", "STARTER_MINUTES_UNCERTAIN"),
    ("NHL", "STARTING_GOALIE_UNCONFIRMED"),
])
def test_premodel_context_is_analyst_review_only_no_rescore_or_flip(sport, flag):
    event = _event(sport)
    winner = _winner(sport)
    ctx = _context(sport)
    ctx["factors"][FACTOR_FAMILIES[sport][0]]["review_flags"] = [
        flag, "MALICIOUS_NARRATIVE_OVERRIDE"
    ]
    original = build_llp_team_intelligence_bundle(event=event, receipts=[winner])
    result = build_llp_team_intelligence_bundle(
        event=event, receipts=[winner], deep_research_context=ctx
    )
    audit = result["deep_research_audit"]
    assert audit["status"] == "SOURCE_CONTEXT_RECORDED_UNCERTIFIED"
    assert audit["factor_families_documented"] == [FACTOR_FAMILIES[sport][0]]
    assert len(audit["factor_families_missing"]) == 9
    assert audit["analyst_review_flags"] == [flag]
    assert audit["analyst_review_required"] is True
    assert audit["model_recheck_required"] is False
    assert audit["upset_volatility_index"] is None
    assert audit["forecast_fragility"] is None
    assert result["markets"] == original["markets"]
    assert result["governed_winner_receipt_id"] == original["governed_winner_receipt_id"]
    assert result["terminal_decision_generated"] is False
    assert result["can_execute"] is False


@pytest.mark.parametrize("sport", ["NBA", "NHL"])
def test_newer_personnel_or_goalie_observation_requires_fitted_refresh(sport):
    ctx = _context(sport)
    ctx["observed_at"] = (MODEL_AT + timedelta(minutes=1)).isoformat()
    audit = build_llp_team_intelligence_bundle(
        event=_event(sport), receipts=[_winner(sport)],
        deep_research_context=ctx,
    )["deep_research_audit"]
    assert audit["status"] == "MATERIAL_CONTEXT_NEWER_THAN_MODEL"
    assert audit["model_recheck_required"] is True
    assert audit["upset_volatility_index"] is None
    assert audit["analyst_review_flags"] == ["FRESH_MODEL_SCORING_REQUIRED"]


@pytest.mark.parametrize("sport,corruption", [
    ("NBA", {"sport": "NHL"}),
    ("NBA", {"source_snapshot_id": "other"}),
    ("NHL", {"source_receipt_id": "wrong"}),
    ("NHL", {"model_version": "guessed-model"}),
    ("NHL", {"observed_at": START.isoformat()}),
    ("NBA", {"observed_at": "not-a-timestamp"}),
])
def test_mismatched_or_postgame_research_cannot_claim_pregame(sport, corruption):
    ctx = _context(sport)
    ctx.update(corruption)
    out = build_llp_team_intelligence_bundle(
        event=_event(sport), receipts=[_winner(sport)],
        deep_research_context=ctx,
    )
    assert out["deep_research_audit"]["status"] == "CONTEXT_INVALID_PROVENANCE"
    assert out["deep_research_audit"]["independently_certified"] is False
    assert out["markets"][0]["calibrated_probability"] == .63


def test_unreceipted_or_postmodel_factor_is_not_documented():
    ctx = _context("NBA")
    first = FACTOR_FAMILIES["NBA"][0]
    ctx["factors"][first]["source_receipt_id"] = ""
    out = inspect_nba_nhl_deep_research(
        event=_event("NBA"), winner_receipt=_winner("NBA"), context=ctx
    )
    assert first in out["factor_families_missing"]
    ctx["factors"][first]["source_receipt_id"] = "known"
    ctx["factors"][first]["observed_at"] = (EVIDENCE_AT + timedelta(minutes=1)).isoformat()
    out = inspect_nba_nhl_deep_research(
        event=_event("NBA"), winner_receipt=_winner("NBA"), context=ctx
    )
    assert first in out["factor_families_missing"]


def test_provisional_winner_is_not_upgraded_by_positive_research_context():
    winner = _winner("NHL")
    winner["model_class"] = "RESEARCH_ONLY"
    winner["probability_publishable"] = False
    ctx = _context("NHL")
    out = build_llp_team_intelligence_bundle(
        event=_event("NHL"), receipts=[winner],
        deep_research_context=ctx,
    )
    assert out["markets"][0]["status"] == "HELD_EVIDENCE_ONLY"
    assert "calibrated_probability" not in out["markets"][0]
    assert out["deep_research_audit"]["status"] == "CONTROLLING_WINNER_RECEIPT_REQUIRED"
    assert out["governed_winner_receipt_id"] is None


def test_readback_projects_optional_nba_research_without_changing_market_status():
    event = _event("NBA")
    source = _winner("NBA")
    saved = {
        "lane": "MONEYLINE", "row_status": "COMPLETED", "row_index": 0,
        "identity": deepcopy(event),
        "detail": {
            "probability_publishable": True,
            "result": {
                **source,
                "selected_side": "BOS",
                "llp_deep_research_context": _context("NBA"),
            },
        },
    }
    page = {"run_id": "r", "status": "OK", "rows": [saved], "returned": 1}
    out = project_llp_intelligence_page(page)
    assert len(out["events"]) == 1
    item = out["events"][0]
    assert item["markets"][0]["status"] == "GOVERNED_RECEIPT_VISIBLE"
    assert item["markets"][0]["calibrated_probability"] == .63
    assert item["deep_research_audit"]["status"] == "SOURCE_CONTEXT_RECORDED_UNCERTIFIED"
    assert out["source_mutated"] is False
    assert out["nfl_pickem_mutated"] is False
    assert out["can_execute"] is False


def test_nfl_intelligence_view_does_not_gain_competing_research_scoring():
    nfl = _event("NBA")
    nfl["sport"] = "NFL"
    winner = _winner("NBA")
    winner["sport"] = "NFL"
    result = build_llp_team_intelligence_bundle(event=nfl, receipts=[winner])
    assert result["deep_research_audit"] is None
    assert result["markets"][0]["calibrated_probability"] == .63
