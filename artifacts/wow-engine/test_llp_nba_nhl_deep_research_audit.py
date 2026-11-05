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
    # Invented UUID-free receipt strings in a saved Daily detail are not
    # independent proof of a pregame event-prediction ledger record.
    assert item["markets"][0]["status"] == "HELD_EVIDENCE_ONLY"
    assert "calibrated_probability" not in item["markets"][0]
    assert "LLP_EVENT_PREDICTION_ID_UNVERIFIED" in item["markets"][0]["reason_codes"]
    assert item["deep_research_audit"]["status"] == "CONTROLLING_WINNER_RECEIPT_REQUIRED"
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


@pytest.mark.parametrize("path", ["observed_at", "model_timestamp", "factor_time"])
@pytest.mark.parametrize("extreme", [
    "0001-01-01T00:00:00+01:00",
    "9999-12-31T23:59:59-01:00",
])
def test_extreme_aware_utc_offsets_fail_closed_not_crash_readback(path, extreme):
    ctx = _context("NBA")
    winner = _winner("NBA")
    if path == "observed_at":
        ctx["observed_at"] = extreme
    elif path == "model_timestamp":
        winner["model_timestamp"] = extreme
    else:
        ctx["factors"][FACTOR_FAMILIES["NBA"][0]]["observed_at"] = extreme
    out = build_llp_team_intelligence_bundle(
        event=_event("NBA"), receipts=[winner],
        deep_research_context=ctx,
    )
    audit = out["deep_research_audit"]
    assert audit["can_execute"] is False
    assert audit["upset_volatility_index"] is None
    assert audit["forecast_fragility"] is None
    if path == "observed_at":
        assert audit["status"] == "CONTEXT_INVALID_PROVENANCE"
    elif path == "model_timestamp":
        # Invalid model-time evidence properly prevents the upstream winner
        # receipt from being governed-visible. The research audit therefore
        # has no controlling winner to attach itself to.
        assert audit["status"] == "CONTROLLING_WINNER_RECEIPT_REQUIRED"
        assert out["markets"][0]["status"] == "HELD_EVIDENCE_ONLY"
        assert "calibrated_probability" not in out["markets"][0]
    else:
        assert audit["status"] == "SOURCE_CONTEXT_RECORDED_UNCERTIFIED"
        assert audit["factor_families_documented"] == []


@pytest.mark.parametrize("sport", ["NBA", "NHL"])
@pytest.mark.parametrize("conflicting_source", ["saved_detail", "result"])
def test_conflicting_persisted_event_id_holds_both_research_and_winner(
    sport, conflicting_source,
):
    event = _event(sport)
    saved = {
        "lane": "MONEYLINE", "row_status": "COMPLETED", "row_index": 4,
        "identity": deepcopy(event),
        "detail": {
            "identity": deepcopy(event),
            "probability_publishable": True,
            "result": {
                **_winner(sport),
                "selected_side": "BOS",
                "llp_deep_research_context": _context(sport),
            },
        },
    }
    if conflicting_source == "saved_detail":
        saved["detail"]["identity"]["official_event_id"] = "unrelated-fixture"
    else:
        saved["detail"]["result"]["official_event_id"] = "unrelated-fixture"
    page = {
        "run_id": "r", "status": "OK", "rows": [saved], "returned": 1,
    }
    out = project_llp_intelligence_page(page)
    assert len(out["events"]) == 1
    item = out["events"][0]
    assert item["official_event_id"] == event["official_event_id"]
    assert item["markets"][0]["status"] == "HELD_EVIDENCE_ONLY"
    assert "calibrated_probability" not in item["markets"][0]
    assert item["governed_winner_receipt_id"] is None
    assert item["deep_research_audit"]["status"] == "CONTROLLING_WINNER_RECEIPT_REQUIRED"
    assert "LLP_RESEARCH_SAVED_EVENT_IDENTITY_CONFLICT" in (
        item["source_diagnostics"]["source_reason_codes"]
    )
    assert out["invalid_event_rows"] == 0
    assert out["can_execute"] is False


@pytest.mark.parametrize("corruption", [
    {"home_team": "CHI"},
    {"event_start_time_utc": (START + timedelta(days=1)).isoformat()},
])
def test_research_rejects_persisted_team_or_fixture_time_conflicts(corruption):
    event = _event("NBA")
    saved_result = _winner("NBA")
    saved_result.update(corruption)
    saved_result.update(
        selected_side="BOS", llp_deep_research_context=_context("NBA"),
    )
    saved = {
        "lane": "MONEYLINE", "row_status": "COMPLETED",
        "identity": deepcopy(event),
        "detail": {
            "result": saved_result,
            "probability_publishable": True,
        },
    }
    out = project_llp_intelligence_page(
        {"status": "OK", "run_id": "r", "rows": [saved]}
    )
    assert len(out["events"]) == 1
    item = out["events"][0]
    assert item["markets"][0]["status"] == "HELD_EVIDENCE_ONLY"
    assert item["deep_research_audit"]["status"] == "CONTROLLING_WINNER_RECEIPT_REQUIRED"


def test_equivalent_offset_event_start_does_not_falsely_hold_research():
    event = _event("NBA")
    persisted = deepcopy(event)
    persisted["event_start_time_utc"] = (
        START.astimezone(timezone(timedelta(hours=-5))).isoformat()
    )
    saved = {
        "lane": "MONEYLINE", "row_status": "COMPLETED",
        "identity": deepcopy(event),
        "detail": {
            "identity": persisted,
            "result": {
                **_winner("NBA"),
                "selected_side": "BOS",
                "llp_deep_research_context": _context("NBA"),
            },
            "probability_publishable": True,
        },
    }
    out = project_llp_intelligence_page(
        {"status": "OK", "run_id": "r", "rows": [saved]}
    )
    assert len(out["events"]) == 1
    assert out["events"][0]["deep_research_audit"]["status"] == (
        "CONTROLLING_WINNER_RECEIPT_REQUIRED"
    )
    assert out["events"][0]["markets"][0]["status"] == "HELD_EVIDENCE_ONLY"


@pytest.mark.parametrize("sport", ["NBA", "NHL"])
@pytest.mark.parametrize("location", ["outer", "nested", "result"])
def test_conflicting_saved_league_alias_fails_closed_independent_of_research(
    sport, location,
):
    event = _event(sport)
    bad_league = "NHL" if sport == "NBA" else "NBA"
    saved = {
        "lane": "MONEYLINE", "row_status": "COMPLETED",
        "identity": deepcopy(event),
        "detail": {
            "identity": deepcopy(event),
            "probability_publishable": True,
            "result": {**_winner(sport), "selected_side": "BOS"},
        },
    }
    source = (
        saved["identity"] if location == "outer"
        else saved["detail"]["identity"] if location == "nested"
        else saved["detail"]["result"]
    )
    source["league"] = bad_league

    def project(with_research):
        row = deepcopy(saved)
        if with_research:
            row["detail"]["result"]["llp_deep_research_context"] = _context(sport)
        return project_llp_intelligence_page(
            {"run_id": "r", "status": "OK", "rows": [row]}
        )["events"][0]

    plain = project(False)
    researched = project(True)
    assert plain["markets"] == researched["markets"]
    assert plain["markets"][0]["status"] == "HELD_EVIDENCE_ONLY"
    assert "calibrated_probability" not in plain["markets"][0]
    assert researched["deep_research_audit"]["status"] == (
        "CONTROLLING_WINNER_RECEIPT_REQUIRED"
    )
    assert "LLP_RESEARCH_SAVED_EVENT_IDENTITY_CONFLICT" in (
        researched["source_diagnostics"]["source_reason_codes"]
    )
    assert plain["can_execute"] is False
    assert researched["can_execute"] is False


@pytest.mark.parametrize("sport", ["NBA", "NHL"])
@pytest.mark.parametrize("bad_field,bad_value", [
    ("official_event_id", "other-game"),
    ("home_team", "CHI"),
    ("league", "WRONG_SPORT"),
])
def test_optional_research_never_flips_saved_winner_publishability(
    sport, bad_field, bad_value,
):
    event = _event(sport)
    saved = {
        "lane": "MONEYLINE", "row_status": "COMPLETED",
        "identity": deepcopy(event),
        "detail": {
            "probability_publishable": True,
            "result": {**_winner(sport), "selected_side": "BOS"},
        },
    }
    # A producer-side contradiction is a core receipt hold, whether the
    # optional research sidecar is absent, valid, or malformed.
    saved["detail"]["result"][bad_field] = bad_value

    def project(research):
        row = deepcopy(saved)
        if research is not None:
            row["detail"]["result"]["llp_deep_research_context"] = research
        return project_llp_intelligence_page(
            {"run_id": "r", "status": "OK", "rows": [row]}
        )["events"][0]

    without = project(None)
    with_valid = project(_context(sport))
    with_invalid = project({"malformed_context": True})
    for item in (with_valid, with_invalid):
        assert item["markets"] == without["markets"]
        assert item["governed_winner_receipt_id"] == (
            without["governed_winner_receipt_id"]
        )
        assert item["can_execute"] is False


@pytest.mark.parametrize("sport", ["NBA", "NHL"])
def test_invalid_optional_research_does_not_withhold_unconflicted_winner(sport):
    event = _event(sport)
    saved = {
        "lane": "MONEYLINE", "row_status": "COMPLETED",
        "identity": deepcopy(event),
        "detail": {
            "probability_publishable": True,
            "result": {**_winner(sport), "selected_side": "BOS"},
        },
    }
    page = {"run_id": "r", "status": "OK", "rows": [saved]}
    baseline = project_llp_intelligence_page(deepcopy(page))["events"][0]
    saved["detail"]["result"]["llp_deep_research_context"] = {
        "sport": "unrelated",
        "observed_at": START.isoformat(),
    }
    researched = project_llp_intelligence_page(page)["events"][0]
    assert researched["markets"] == baseline["markets"]
    assert researched["markets"][0]["status"] == "HELD_EVIDENCE_ONLY"
    assert "calibrated_probability" not in researched["markets"][0]
    assert researched["governed_winner_receipt_id"] == (
        baseline["governed_winner_receipt_id"]
    )
    # No verified immutable winner means research cannot claim a controlling
    # source receipt, regardless of whether the optional context is malformed.
    assert researched["deep_research_audit"]["status"] == (
        "CONTROLLING_WINNER_RECEIPT_REQUIRED"
    )


# Independent event-prediction ledger proof: the fake below models the read-only
# Supabase query interface while storing a separately supplied immutable record.
# The Daily row alone is never trusted as its own receipt verification.
LEDGER_ID = "fcd3f8bf-635e-4d01-90e7-952de6cc53f1"
SNAPSHOT_ID = "f0e7ac5e-997d-4caf-b584-e7abb56aa910"


class _EventLedger:
    def __init__(self, rows):
        self.rows = rows
        self.table_name = None
        self.selected = None
        self.prediction_id = None
        self.limit_count = None
        self.queries = 0

    def table(self, name):
        self.table_name = name
        return self

    def select(self, columns):
        self.selected = columns
        return self

    def eq(self, key, value):
        assert key == "event_prediction_id"
        self.prediction_id = value
        return self

    def limit(self, count):
        self.limit_count = count
        return self

    def execute(self):
        from types import SimpleNamespace
        assert self.table_name == "wow_event_predictions"
        assert self.limit_count == 2
        assert "calibrated_home_probability" in self.selected
        self.queries += 1
        return SimpleNamespace(data=[
            row for row in self.rows
            if row.get("event_prediction_id") == self.prediction_id
        ])


def _ledger_case(sport="NBA"):
    event = _event(sport)
    winner = _winner(sport)
    winner["source_receipt_id"] = LEDGER_ID
    winner["source_snapshot_id"] = SNAPSHOT_ID
    ctx = _context(sport)
    ctx["source_receipt_id"] = LEDGER_ID
    ctx["source_snapshot_id"] = SNAPSHOT_ID
    source = {
        **winner, "selected_side": "BOS",
        "llp_deep_research_context": ctx,
    }
    row = {
        "lane": "MONEYLINE", "row_status": "COMPLETED",
        "identity": deepcopy(event),
        "detail": {
            "result": source,
            "probability_publishable": True,
        },
    }
    ledger = {
        "event_prediction_id": LEDGER_ID,
        "created_at": SOURCE_AT.isoformat(),
        "official_event_id": event["official_event_id"],
        "event_start_time": event["event_start_time_utc"],
        "sport": sport,
        "market_family": "OUTRIGHT_WINNER",
        "settlement_basis": (
            "FULL_GAME_MONEYLINE_INCLUDING_OVERTIME_SHOOTOUT"
            if sport == "NHL" else "FULL_GAME_INCLUDING_OVERTIME"
        ),
        "home_team": "BOS",
        "away_team": event["away_team"],
        "selected_participant": "BOS",
        "model_version": winner["model_version"],
        "calibration_version": winner["calibration_version"],
        "model_timestamp": winner["model_timestamp"],
        "source_snapshot_timestamp": winner["source_snapshot_timestamp"],
        "source_snapshot_id": SNAPSHOT_ID,
        "calibrated_home_probability": .63,
        "calibrated_home_lower_bound": .57,
        "calibrated_home_upper_bound": .69,
        "calibrated_away_probability": .37,
        "calibrated_away_lower_bound": .31,
        "calibrated_away_upper_bound": .43,
        "probability_publishable": True,
        "rank_eligible": True,
        "can_execute": False,
        "locked_at": (MODEL_AT + timedelta(minutes=1)).isoformat(),
        "terminal_reduced_at": (MODEL_AT + timedelta(minutes=2)).isoformat(),
    }
    page = {"run_id": "r", "status": "OK", "rows": [row]}
    return page, ledger


@pytest.mark.parametrize("sport", ["NBA", "NHL"])
def test_genuine_exact_immutable_event_prediction_ledger_receipt_only_visible_positive(sport):
    page, ledger = _ledger_case(sport)
    db = _EventLedger([ledger])
    out = project_llp_intelligence_page(page, db=db)
    assert db.queries == 1
    item = out["events"][0]
    assert item["markets"][0]["status"] == "GOVERNED_RECEIPT_VISIBLE"
    assert item["markets"][0]["calibrated_probability"] == .63
    assert item["source_diagnostics"]["event_prediction_receipt_verified"] is True
    assert item["deep_research_audit"]["status"] == "SOURCE_CONTEXT_RECORDED_UNCERTIFIED"
    assert item["governed_winner_receipt_id"] == LEDGER_ID
    assert item["can_execute"] is False


@pytest.mark.parametrize("sport", ["NBA", "NHL"])
@pytest.mark.parametrize("corruption", [
    "nonexistent", "wrong_event", "wrong_version", "wrong_snapshot",
    "late_lock", "wrong_probability", "not_published",
    "wrong_settlement", "duplicate",
])
def test_fabricated_or_mismatched_ledger_never_promotes_daily_winner(sport, corruption):
    page, ledger = _ledger_case(sport)
    if corruption == "nonexistent":
        rows = []
    elif corruption == "duplicate":
        rows = [ledger, deepcopy(ledger)]
    else:
        altered = deepcopy(ledger)
        if corruption == "wrong_event":
            altered["official_event_id"] = "another-event"
        elif corruption == "wrong_version":
            altered["model_version"] = "wrong-fitted-model"
        elif corruption == "wrong_snapshot":
            altered["source_snapshot_id"] = "another-snapshot"
        elif corruption == "late_lock":
            altered["locked_at"] = START.isoformat()
        elif corruption == "wrong_probability":
            altered["calibrated_home_probability"] = .88
        elif corruption == "not_published":
            altered["probability_publishable"] = False
        elif corruption == "wrong_settlement":
            altered["settlement_basis"] = (
                "REGULATION_TIME_THREE_WAY"
                if sport == "NHL" else "REGULATION_ONLY"
            )
        rows = [altered]
    db = _EventLedger(rows)
    out = project_llp_intelligence_page(page, db=db)
    item = out["events"][0]
    assert item["markets"][0]["status"] == "HELD_EVIDENCE_ONLY"
    assert "calibrated_probability" not in item["markets"][0]
    assert item["source_diagnostics"]["event_prediction_receipt_verified"] is False
    assert item["governed_winner_receipt_id"] is None
    assert item["deep_research_audit"]["status"] == "CONTROLLING_WINNER_RECEIPT_REQUIRED"
    assert item["can_execute"] is False


def test_ledger_lookup_failure_holds_without_leaking_provider_text():
    page, ledger = _ledger_case()
    class _Down:
        def table(self, name):
            raise RuntimeError("secret internal endpoint failure")
    out = project_llp_intelligence_page(page, db=_Down())
    item = out["events"][0]
    assert item["markets"][0]["status"] == "HELD_EVIDENCE_ONLY"
    assert item["source_diagnostics"]["event_prediction_receipt_verified"] is False
    assert "LLP_EVENT_PREDICTION_LOOKUP_UNAVAILABLE" in (
        item["source_diagnostics"]["source_reason_codes"]
    )
    assert "secret" not in str(item)
