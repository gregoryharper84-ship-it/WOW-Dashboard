from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from v17.nhl_starting_goalie_evidence import (
    FULL_GAME_SETTLEMENT,
    GAMECENTER_PREFIX,
    GOALIE_PRIOR_SHOTS,
    GOALIE_PRIOR_SV,
    goalie_replacement_scenarios,
    validate_nhl_full_game_model_contract,
    verify_nhl_goalie_receipt,
    verify_nhl_starting_goalie_evidence,
)

NOW = datetime(2026, 11, 5, 17, 0, tzinfo=timezone.utc)
START_AT = NOW + timedelta(hours=3)
EVENT_ID = "2026020219"


def _iso(value: datetime) -> str:
    return value.isoformat()


def _identity() -> dict:
    return {
        "event_id": EVENT_ID,
        "event_start_time": _iso(START_AT),
        "home_team": "BOS",
        "away_team": "NYR",
        "identity_provider": "NHL_PUBLIC_WEB_API",
        "identity_source": "https://api-web.nhle.com/v1/club-schedule-season/BOS/20262027",
        "identity_verified_at": _iso(NOW - timedelta(minutes=5)),
        "market_features_used": False,
        "prediction_authority": False,
        "can_execute": False,
    }


def _evidence() -> dict:
    return {
        "sport": "NHL",
        "official_event_id": EVENT_ID,
        "home_team": "BOS",
        "away_team": "NYR",
        "event_start_time_utc": _iso(START_AT),
        "source_snapshot_id": "sha256:" + "c" * 64,
        "source_snapshot_timestamp": _iso(NOW - timedelta(minutes=20)),
        "confirmation": {
            "source_provider": "NHL_PUBLIC_WEB_API",
            "source_url": f"{GAMECENTER_PREFIX}{EVENT_ID}/landing",
            "method": "OFFICIAL_GAMECENTER_STARTER_ANNOUNCEMENT",
            "snapshot_id": "gc-snapshot-1",
            "captured_at": _iso(NOW - timedelta(minutes=5)),
            "market_features_used": False,
            "prediction_authority": False,
            "can_execute": False,
            "starters": {
                "home": {
                    "goalie_id": "8478402",
                    "team": "BOS",
                    "status": "CONFIRMED",
                    "observed_at": _iso(NOW - timedelta(minutes=10)),
                    "prior_shots_against": 500,
                    "prior_saves": 460,
                },
                "away": {
                    "goalie_id": "8479979",
                    "team": "NYR",
                    "status": "CONFIRMED",
                    "observed_at": _iso(NOW - timedelta(minutes=12)),
                    "prior_shots_against": 400,
                    "prior_saves": 372,
                },
            },
            "replacements": {
                "home": {
                    "goalie_id": "8480001",
                    "team": "BOS",
                    "status": "BACKUP_ROSTERED",
                    "prior_shots_against": 100,
                    "prior_saves": 90,
                },
                "away": {
                    "goalie_id": "8480002",
                    "team": "NYR",
                    "status": "PROJECTED_BACKUP",
                    "prior_shots_against": 80,
                    "prior_saves": 70,
                },
            },
        },
    }


def _verify(evidence=None, identity=None):
    return verify_nhl_starting_goalie_evidence(
        evidence if evidence is not None else _evidence(),
        as_of_utc=NOW,
        resolved_identity=identity if identity is not None else _identity(),
    )


def test_official_confirmation_passes_evidence_only_without_certification():
    result = _verify()
    assert result["status"] == "GOALIE_EVIDENCE_PASS_NOT_CERTIFICATION"
    assert result["blockers"] == []
    assert result["starter_confirmed"] is True
    assert result["research_only"] is True
    assert result["probability_publishable"] is False
    assert result["rank_eligible"] is False
    assert result["can_execute"] is False


def test_missing_evidence_holds_closed():
    result = verify_nhl_starting_goalie_evidence(
        None, as_of_utc=NOW, resolved_identity=_identity()
    )
    assert result["status"] == "HELD"
    assert result["blockers"] == ["NHL_GOALIE_EVIDENCE_MISSING"]
    assert result["starter_confirmed"] is False
    assert result["can_execute"] is False


def test_confirmation_source_provider_url_and_method_must_be_official():
    for field, value, blocker in (
        ("source_provider", "ESPN_SCOREBOARD", "NHL_GOALIE_CONFIRMATION_PROVIDER_UNVERIFIED"),
        ("source_url", "https://site.api.espn.com/claims/1", "NHL_GOALIE_CONFIRMATION_SOURCE_UNVERIFIED"),
        ("source_url", GAMECENTER_PREFIX + "2026000000/landing", "NHL_GOALIE_CONFIRMATION_SOURCE_UNVERIFIED"),
        ("method", "TEAM_REPORTER_CLAIM", "NHL_GOALIE_CONFIRMATION_METHOD_UNVERIFIED"),
        ("method", "", "NHL_GOALIE_CONFIRMATION_METHOD_UNVERIFIED"),
    ):
        package = _evidence()
        package["confirmation"][field] = value
        result = _verify(package)
        assert result["status"] == "HELD", field
        assert blocker in result["blockers"], field


def test_confirmation_must_be_fresh_and_pre_event():
    for captured_at, blocker in (
        (_iso(NOW - timedelta(hours=2)), "NHL_GOALIE_CONFIRMATION_STALE"),
        (_iso(NOW + timedelta(minutes=1)), "NHL_GOALIE_CONFIRMATION_STALE"),
        ("2026-11-05T16:55:00", "NHL_GOALIE_CONFIRMATION_STALE"),
    ):
        package = _evidence()
        package["confirmation"]["captured_at"] = captured_at
        result = _verify(package)
        assert result["status"] == "HELD"
        assert blocker in result["blockers"]

    started = _evidence()
    started["event_start_time_utc"] = _iso(NOW - timedelta(minutes=1))
    identity = _identity()
    identity["event_start_time"] = _iso(NOW - timedelta(minutes=1))
    result = _verify(started, identity)
    assert result["status"] == "HELD"
    assert "NHL_GOALIE_EVENT_NOT_PREGAME" in result["blockers"]


def test_source_snapshot_and_identity_chronology_hold_closed():
    for mutate, blocker in (
        (lambda e: e.__setitem__("source_snapshot_id", "  "), "NHL_GOALIE_SOURCE_SNAPSHOT_MISSING"),
        (lambda e: e.__setitem__("source_snapshot_timestamp", "2026-11-05T16:00:00"), "NHL_GOALIE_SOURCE_TIMESTAMP_INVALID"),
        (lambda e: e.__setitem__("source_snapshot_timestamp", _iso(NOW + timedelta(minutes=1))), "NHL_GOALIE_SOURCE_SNAPSHOT_IN_FUTURE"),
        (lambda e: e.__setitem__("source_snapshot_timestamp", _iso(START_AT)), "NHL_GOALIE_SOURCE_SNAPSHOT_AFTER_KICKOFF"),
    ):
        package = _evidence()
        mutate(package)
        result = _verify(package)
        assert result["status"] == "HELD"
        assert blocker in result["blockers"]

    missing = verify_nhl_starting_goalie_evidence(
        _evidence(), as_of_utc=NOW, resolved_identity=None
    )
    assert missing["status"] == "HELD"
    assert "NHL_GOALIE_CANONICAL_IDENTITY_RECEIPT_MISSING" in missing["blockers"]


def test_identity_receipt_mismatches_hold_closed():
    for field, value in (
        ("event_id", "0000000000"),
        ("identity_provider", "ESPN_SCOREBOARD"),
        ("identity_source", "https://site.api.espn.com/claims/1"),
        ("identity_verified_at", _iso(NOW - timedelta(hours=48))),
        ("event_start_time", _iso(START_AT + timedelta(hours=1))),
        ("home_team", "CAR"),
        ("market_features_used", True),
        ("prediction_authority", True),
        ("can_execute", True),
    ):
        identity = _identity()
        identity[field] = value
        result = _verify(identity=identity)
        assert result["status"] == "HELD", field
        assert "NHL_GOALIE_CANONICAL_IDENTITY_RECEIPT_MISMATCH" in result["blockers"], field


def test_event_and_team_identity_are_validated_first():
    for field, value, blocker in (
        ("official_event_id", "espn-401234", "NHL_GOALIE_EVENT_IDENTITY_INVALID"),
        ("home_team", "UNKNOWN FRANCHISE", "NHL_GOALIE_TEAM_IDENTITY_UNVERIFIED"),
        ("away_team", "BOS", "NHL_GOALIE_DUPLICATE_OPPONENT_IDENTITY"),
        ("sport", "NBA", "NHL_GOALIE_SPORT_MISMATCH"),
    ):
        package = _evidence()
        package[field] = value
        result = _verify(package)
        assert result["status"] == "HELD", field
        assert blocker in result["blockers"], field


def test_unconfirmed_starters_and_wrong_team_hold_closed():
    for side, field, value, blocker in (
        ("home", "status", "PROJECTED", "NHL_GOALIE_HOME_STARTER_NOT_CONFIRMED"),
        ("away", "team", "BOS", "NHL_GOALIE_AWAY_STARTER_TEAM_MISMATCH"),
        ("home", "observed_at", _iso(NOW - timedelta(hours=2)), "NHL_GOALIE_HOME_STARTER_STALE"),
    ):
        package = _evidence()
        package["confirmation"]["starters"][side][field] = value
        result = _verify(package)
        assert result["status"] == "HELD"
        assert blocker in result["blockers"]


def test_duplicate_or_missing_starter_identity_holds_closed():
    package = _evidence()
    package["confirmation"]["starters"]["away"]["goalie_id"] = "8478402"
    result = _verify(package)
    assert result["status"] == "HELD"
    assert "NHL_GOALIE_DUPLICATE_STARTER_IDENTITY" in result["blockers"]

    missing = _evidence()
    missing["confirmation"]["starters"]["home"]["goalie_id"] = ""
    result = _verify(missing)
    assert result["status"] == "HELD"
    assert "NHL_GOALIE_HOME_STARTER_ID_INVALID" in result["blockers"]


def test_replacement_scenario_validation_is_fail_closed():
    for mutate, blocker in (
        (lambda r: r.pop("away"), "NHL_GOALIE_AWAY_REPLACEMENT_MISSING"),
        (lambda r: r["home"].__setitem__("goalie_id", "8478402"), "NHL_GOALIE_HOME_REPLACEMENT_DUPLICATES_STARTER"),
        (lambda r: r["away"].__setitem__("status", "EXPECTED"), "NHL_GOALIE_AWAY_REPLACEMENT_STATUS_UNVERIFIED"),
        (lambda r: r["home"].__setitem__("prior_saves", 101), "NHL_GOALIE_HOME_REPLACEMENT_WORKLOAD_INVALID"),
        (lambda r: r["away"].__setitem__("prior_shots_against", 0), "NHL_GOALIE_AWAY_REPLACEMENT_WORKLOAD_INVALID"),
        (lambda r: r["away"].__setitem__("team", "BOS"), "NHL_GOALIE_AWAY_REPLACEMENT_TEAM_MISMATCH"),
    ):
        package = _evidence()
        mutate(package["confirmation"]["replacements"])
        result = _verify(package)
        assert result["status"] == "HELD"
        assert blocker in result["blockers"]


def test_model_or_market_authority_claims_hold_closed():
    package = _evidence()
    package["confirmation"]["prediction_authority"] = True
    result = _verify(package)
    assert result["status"] == "HELD"
    assert "NHL_GOALIE_CONFIRMATION_AUTHORITY_CLAIMED" in result["blockers"]


def test_replacement_scenarios_use_preregistered_shrinkage_prior():
    result = goalie_replacement_scenarios(
        _evidence(), as_of_utc=NOW, resolved_identity=_identity()
    )
    assert result["status"] == "GOALIE_SCENARIOS_RESEARCH_ONLY"
    assert result["blockers"] == []
    assert result["shrinkage_prior_shots"] == GOALIE_PRIOR_SHOTS
    assert result["shrinkage_prior_save_pct"] == pytest.approx(GOALIE_PRIOR_SV)
    home = result["scenarios"]["home"]
    away = result["scenarios"]["away"]
    assert home["starter_goalie_id"] == "8478402"
    assert home["replacement_goalie_id"] == "8480001"
    assert home["starter_shrunk_save_pct"] == pytest.approx((460 + 270) / 800)
    assert home["replacement_shrunk_save_pct"] == pytest.approx((90 + 270) / 400)
    assert home["replacement_save_pct_delta"] == pytest.approx(0.9125 - 0.9)
    assert away["starter_shrunk_save_pct"] == pytest.approx((372 + 270) / 700)
    assert away["replacement_shrunk_save_pct"] == pytest.approx((70 + 270) / 380)
    assert home["replacement_basis"] == "BACKUP_ROSTERED"
    assert away["replacement_basis"] == "PROJECTED_BACKUP"
    assert result["goalie_identity_snapshot_id"] == "gc-snapshot-1"
    assert result["identity_receipt"]["event_id"] == EVENT_ID
    assert len(result["evidence_sha256"]) == 64
    assert len(result["identity_receipt_sha256"]) == 64
    assert result["goalie_feature_consumable"] is False
    assert result["calibration_status"] == "NOT_CALIBRATED_RESEARCH_ONLY"
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False


def test_replacement_scenarios_hold_without_verified_starters():
    package = _evidence()
    package["confirmation"]["starters"]["home"]["status"] = "PROJECTED"
    result = goalie_replacement_scenarios(
        package, as_of_utc=NOW, resolved_identity=_identity()
    )
    assert result["status"] == "HELD"
    assert "NHL_GOALIE_HOME_STARTER_NOT_CONFIRMED" in result["blockers"]
    assert result["scenarios"] is None
    assert result["can_execute"] is False


def test_replacement_scenarios_hold_on_missing_starter_workload():
    package = _evidence()
    package["confirmation"]["starters"]["home"].pop("prior_shots_against")
    result = goalie_replacement_scenarios(
        package, as_of_utc=NOW, resolved_identity=_identity()
    )
    assert result["status"] == "HELD"
    assert "NHL_GOALIE_HOME_STARTER_WORKLOAD_INVALID" in result["blockers"]
    assert result["starter_confirmed"] is False
    assert result["scenarios"] is None


def test_stored_goalie_receipt_round_trips_and_tampering_is_rejected():
    receipt = goalie_replacement_scenarios(
        _evidence(), as_of_utc=NOW, resolved_identity=_identity()
    )
    verified = verify_nhl_goalie_receipt(receipt)
    assert verified["status"] == "VERIFIED_RESEARCH_RECEIPT"
    assert verified["blockers"] == []
    assert verified["probability_publishable"] is False
    assert verified["can_execute"] is False

    tampered = deepcopy(receipt)
    tampered["scenarios"]["home"]["starter_shrunk_save_pct"] = 0.99
    rejected = verify_nhl_goalie_receipt(tampered)
    assert rejected["status"] == "HELD"
    assert "NHL_GOALIE_RECEIPT_SCENARIO_MISMATCH" in rejected["blockers"]

    forged = deepcopy(receipt)
    forged["source_evidence"]["confirmation"]["starters"]["home"]["prior_saves"] = 100
    rejected = verify_nhl_goalie_receipt(forged)
    assert rejected["status"] == "HELD"
    assert "NHL_GOALIE_RECEIPT_EVIDENCE_DIGEST_MISMATCH" in rejected["blockers"]


def _modeling() -> dict:
    segment = {"status": "MODELED", "artifact_id": "nhl-segment-artifact-1"}
    return {
        "settlement_basis": FULL_GAME_SETTLEMENT,
        "regulation_only_probability_reused_as_full_game": False,
        "overtime_included_in_final_score": True,
        "shootout_included_in_final_score": True,
        "five_on_five_expected_goals": dict(segment),
        "special_teams_interaction": dict(segment),
        "overtime_segment": dict(segment),
        "shootout_segment": dict(segment),
    }


def test_full_game_contract_requires_every_segment_without_publishing():
    result = validate_nhl_full_game_model_contract(_modeling())
    assert result["status"] == "FULL_GAME_MODEL_CONTRACT_PASS_NOT_CERTIFICATION"
    assert result["blockers"] == []
    assert result["settlement_basis"] == FULL_GAME_SETTLEMENT
    assert set(result["modeled_segments"]) == {
        "five_on_five_expected_goals",
        "special_teams_interaction",
        "overtime_segment",
        "shootout_segment",
    }
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False


def test_full_game_contract_rejects_regulation_only_and_missing_segments():
    package = _modeling()
    package["settlement_basis"] = "REGULATION_3_WAY"
    package["regulation_only_probability_reused_as_full_game"] = True
    package["overtime_included_in_final_score"] = False
    package["shootout_included_in_final_score"] = False
    for segment in (
        "five_on_five_expected_goals",
        "special_teams_interaction",
        "overtime_segment",
        "shootout_segment",
    ):
        package.pop(segment)
    result = validate_nhl_full_game_model_contract(package)

    assert result["status"] == "HELD"
    for blocker in (
        "NHL_FULL_GAME_SETTLEMENT_BASIS_MISMATCH",
        "NHL_REGULATION_ONLY_MODEL_REUSE",
        "NHL_OVERTIME_NOT_INCLUDED_IN_FINAL_SCORE",
        "NHL_SHOOTOUT_NOT_INCLUDED_IN_FINAL_SCORE",
        "NHL_FIVE_ON_FIVE_EXPECTED_GOALS_NOT_MODELED",
        "NHL_SPECIAL_TEAMS_INTERACTION_NOT_MODELED",
        "NHL_OVERTIME_SEGMENT_NOT_MODELED",
        "NHL_SHOOTOUT_SEGMENT_NOT_MODELED",
    ):
        assert blocker in result["blockers"]
    assert result["probability_publishable"] is False


def test_full_game_contract_rejects_claimed_but_unbound_segments():
    package = _modeling()
    package["shootout_segment"] = {"status": "MODELED", "artifact_id": "  "}
    package["overtime_segment"] = {"status": "PROJECTED", "artifact_id": "x"}
    result = validate_nhl_full_game_model_contract(package)
    assert result["status"] == "HELD"
    assert "NHL_SHOOTOUT_SEGMENT_NOT_MODELED" in result["blockers"]
    assert "NHL_OVERTIME_SEGMENT_NOT_MODELED" in result["blockers"]

    missing = validate_nhl_full_game_model_contract(None)
    assert missing["status"] == "HELD"
    assert missing["blockers"] == ["NHL_FULL_GAME_MODELING_EVIDENCE_MISSING"]
    assert missing["can_execute"] is False
