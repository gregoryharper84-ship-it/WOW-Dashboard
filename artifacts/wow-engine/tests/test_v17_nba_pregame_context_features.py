from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from v17.nba_pregame_context_features import (
    CONTEXT_FEATURE_NAMES,
    derive_nba_pregame_context_features,
    verify_nba_pregame_context_receipt,
)

NOW = datetime(2026, 11, 5, 17, 0, tzinfo=timezone.utc)
EVENT_AT = NOW + timedelta(hours=5)
PLANNED = (34.0, 34.0, 34.0, 34.0, 32.0, 28.0, 24.0, 20.0)
VALUES = (8.0, 7.0, 6.0, 5.0, 4.0, 3.0, 2.0, 1.0)


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _rotation(side: str, *, injured: bool = False) -> list[dict]:
    players = []
    for index, (minutes, value) in enumerate(zip(PLANNED, VALUES)):
        entry = {
            "player_id": str(100 + index + (0 if side == "home" else 50)),
            "player_value": value,
            "planned_minutes": minutes,
            "expected_minutes": minutes,
            "status": "AVAILABLE",
            "projected_starter": index < 5,
            "prior_game_starter": index < 4,
            "replacement": None,
        }
        if injured and side == "home" and index == 5:
            entry.update(
                expected_minutes=0.0,
                status="OUT",
                replacement={
                    "player_id": "900",
                    "player_value": 2.0,
                    "expected_minutes": 28.0,
                },
            )
        players.append(entry)
    return players


def _evidence(**overrides) -> dict:
    package = {
        "sport": "NBA",
        "official_event_id": "espn-401999001",
        "home_team_id": "espn-1",
        "away_team_id": "espn-2",
        "event_start_time_utc": _iso(EVENT_AT),
        "source_snapshot_id": "sha256:" + "a" * 64,
        "source_snapshot_timestamp": _iso(NOW - timedelta(minutes=10)),
        "players": {"home": _rotation("home"), "away": _rotation("away")},
        "shot_quality": {
            "home": {"own_offense_index": 1.0, "defense_allowed_index": 0.4},
            "away": {"own_offense_index": 0.8, "defense_allowed_index": 0.5},
        },
        "pace": {"home": 100.0, "away": 95.0},
        "shot_profile": {
            "home": {"three_point_rate": 0.40, "rim_rate": 0.35, "midrange_rate": 0.25},
            "away": {"three_point_rate": 0.30, "rim_rate": 0.45, "midrange_rate": 0.25},
        },
        "travel": {
            "home": {"rest_days": 2, "travel_miles_7d": 0.0, "time_zone_shift_hours": 0.0},
            "away": {"rest_days": 0, "travel_miles_7d": 2500.0, "time_zone_shift_hours": 3.0},
        },
        "recent_margins": {"home": [5.0, -3.0, 7.0, 2.0, 10.0], "away": [1.0] * 5},
    }
    package.update(overrides)
    return package


def _derive(package=None):
    return derive_nba_pregame_context_features(
        package if package is not None else _evidence(), as_of_utc=NOW
    )


def test_valid_server_owned_evidence_derives_every_preregistered_factor():
    result = _derive()

    assert result["status"] == "PASS"
    assert result["blockers"] == []
    assert result["code"] is None
    features = result["features"]
    for side in ("home", "away"):
        assert set(features[side]) == set(CONTEXT_FEATURE_NAMES)
    assert features["home"]["minutes_weighted_player_value"] == pytest.approx(4.85)
    assert features["home"]["availability_minutes_share"] == pytest.approx(1.0)
    assert features["home"]["replacement_quality_delta"] == pytest.approx(0.0)
    assert features["home"]["lineup_continuity"] == pytest.approx(0.8)
    assert features["home"]["opponent_adjusted_shot_quality"] == pytest.approx(0.75)
    assert features["away"]["opponent_adjusted_shot_quality"] == pytest.approx(0.6)
    assert features["home"]["pace"] == pytest.approx(100.0)
    assert features["home"]["pace_style_mismatch"] == pytest.approx(0.1)
    assert features["home"]["rest_days"] == pytest.approx(2.0)
    assert features["home"]["back_to_back"] == pytest.approx(0.0)
    assert features["away"]["back_to_back"] == pytest.approx(1.0)
    assert features["away"]["travel_miles_7d"] == pytest.approx(2500.0)
    assert features["away"]["time_zone_shift_hours"] == pytest.approx(3.0)
    assert features["home"]["recent_margin_variance"] == pytest.approx(24.7)
    assert features["away"]["recent_margin_variance"] == pytest.approx(0.0)
    assert len(result["evidence_sha256"]) == 64
    assert len(result["factor_vector_sha256"]) == 64
    assert result["source_evidence"] == _evidence()
    assert result["research_only"] is True
    assert result["fitted_feature_vector_extension"] is False
    assert result["calibration_status"] == "NOT_CALIBRATED_RESEARCH_ONLY"
    assert result["probability_publishable"] is False
    assert result["rank_eligible"] is False
    assert result["can_execute"] is False


def test_injured_starter_uses_minutes_weighted_replacement_arithmetic():
    package = _evidence()
    package["players"]["home"] = _rotation("home", injured=True)
    result = _derive(package)

    assert result["status"] == "PASS"
    home = result["features"]["home"]
    assert home["minutes_weighted_player_value"] == pytest.approx(1080.0 / 240.0)
    assert home["availability_minutes_share"] == pytest.approx(212.0 / 240.0)
    assert home["replacement_quality_delta"] == pytest.approx(28.0 * (2.0 - 3.0) / 240.0)
    assert home["lineup_continuity"] == pytest.approx(0.8)


def test_missing_evidence_holds_closed():
    result = derive_nba_pregame_context_features(None, as_of_utc=NOW)
    assert result["status"] == "HELD"
    assert result["code"] == "MODEL_INPUTS_INSUFFICIENT"
    assert result["blockers"] == ["NBA_CONTEXT_EVIDENCE_MISSING"]
    assert result["features"] is None
    assert result["can_execute"] is False


def test_non_nba_sport_holds_closed():
    result = _derive(_evidence(sport="WNBA"))
    assert result["status"] == "HELD"
    assert "NBA_CONTEXT_SPORT_MISMATCH" in result["blockers"]


def test_provider_aliases_and_duplicate_teams_hold_closed():
    for home, away, blocker in (
        ("1", "espn-2", "NBA_CONTEXT_TEAM_IDENTITY_INVALID"),
        ("espn-1", "espn-1", "NBA_CONTEXT_DUPLICATE_OPPONENT_IDENTITY"),
        ("espn-1", "espn-2", None),
    ):
        result = _derive(_evidence(home_team_id=home, away_team_id=away))
        if blocker is None:
            assert result["status"] == "PASS"
        else:
            assert result["status"] == "HELD"
            assert blocker in result["blockers"]
    result = _derive(_evidence(official_event_id="401999001"))
    assert result["status"] == "HELD"
    assert "NBA_CONTEXT_EVENT_IDENTITY_INVALID" in result["blockers"]


def test_naive_or_future_source_timestamps_hold_closed():
    naive = _evidence(source_snapshot_timestamp="2026-11-05T16:50:00")
    result = _derive(naive)
    assert "NBA_CONTEXT_SOURCE_TIMESTAMP_INVALID" in result["blockers"]

    future = _evidence(source_snapshot_timestamp=_iso(NOW + timedelta(minutes=1)))
    result = _derive(future)
    assert "NBA_CONTEXT_SOURCE_SNAPSHOT_IN_FUTURE" in result["blockers"]

    missing = _evidence(source_snapshot_id="  ")
    result = _derive(missing)
    assert "NBA_CONTEXT_SOURCE_SNAPSHOT_MISSING" in result["blockers"]


def test_source_snapshot_at_or_after_kickoff_never_passes():
    result = _derive(_evidence(source_snapshot_timestamp=_iso(EVENT_AT)))
    assert result["status"] == "HELD"
    assert "NBA_CONTEXT_SOURCE_SNAPSHOT_AFTER_KICKOFF" in result["blockers"]


def test_as_of_at_or_after_kickoff_is_not_pregame():
    result = derive_nba_pregame_context_features(_evidence(), as_of_utc=EVENT_AT)
    assert result["status"] == "HELD"
    assert "NBA_CONTEXT_NOT_PREGAME" in result["blockers"]
    with pytest.raises(ValueError):
        derive_nba_pregame_context_features(_evidence(), as_of_utc=datetime(2026, 11, 5))


def test_rotation_minutes_must_equal_regulation_total():
    package = _evidence()
    package["players"]["home"][0]["planned_minutes"] = 33.0
    package["players"]["home"][0]["expected_minutes"] = 33.0
    result = _derive(package)
    assert result["status"] == "HELD"
    assert "NBA_CONTEXT_HOME_ROTATION_MINUTES_INVALID" in result["blockers"]


def test_duplicate_player_identity_holds_closed():
    package = _evidence()
    package["players"]["away"][1]["player_id"] = package["players"]["away"][0]["player_id"]
    result = _derive(package)
    assert result["status"] == "HELD"
    assert "NBA_CONTEXT_AWAY_PLAYER_ID_DUPLICATE" in result["blockers"]


def test_out_player_cannot_retain_expected_minutes():
    package = _evidence()
    package["players"]["home"][5]["status"] = "OUT"
    result = _derive(package)
    assert result["status"] == "HELD"
    assert "NBA_CONTEXT_HOME_OUT_PLAYER_HAS_MINUTES" in result["blockers"]


def test_lost_minutes_require_a_replacement_scenario():
    package = _evidence()
    package["players"]["home"][5]["expected_minutes"] = 10.0
    result = _derive(package)
    assert result["status"] == "HELD"
    assert "NBA_CONTEXT_HOME_REPLACEMENT_MISSING" in result["blockers"]


def test_replacement_cannot_reuse_a_rotation_identity():
    package = _evidence()
    package["players"]["home"][5]["expected_minutes"] = 0.0
    package["players"]["home"][5]["replacement"] = {
        "player_id": package["players"]["home"][6]["player_id"],
        "player_value": 2.0,
        "expected_minutes": 28.0,
    }
    result = _derive(package)
    assert result["status"] == "HELD"
    assert "NBA_CONTEXT_HOME_REPLACEMENT_INVALID" in result["blockers"]


def test_nonfinite_and_boolean_numbers_hold_closed():
    for field, value in (
        ("player_value", float("nan")),
        ("planned_minutes", True),
        ("expected_minutes", float("inf")),
    ):
        package = _evidence()
        package["players"]["home"][0][field] = value
        result = _derive(package)
        assert result["status"] == "HELD"
        assert "NBA_CONTEXT_HOME_ROTATION_ENTRY_INVALID" in result["blockers"]


def test_shot_profile_must_be_a_normalized_distribution():
    package = _evidence()
    package["shot_profile"]["away"]["rim_rate"] = 0.40
    result = _derive(package)
    assert result["status"] == "HELD"
    assert "NBA_CONTEXT_AWAY_SHOT_PROFILE_INVALID" in result["blockers"]


def test_insufficient_or_invalid_margin_history_holds_closed():
    result = _derive(_evidence(recent_margins={"home": [1.0] * 4, "away": [1.0] * 5}))
    assert "NBA_CONTEXT_HOME_MARGIN_HISTORY_INSUFFICIENT" in result["blockers"]

    result = _derive(_evidence(recent_margins={"home": [1.0] * 5, "away": ["x"] * 5}))
    assert "NBA_CONTEXT_AWAY_MARGIN_HISTORY_INVALID" in result["blockers"]


def test_missing_shot_quality_pace_and_travel_hold_closed():
    result = _derive(_evidence(shot_quality={"home": {"own_offense_index": 1.0}}))
    assert "NBA_CONTEXT_AWAY_SHOT_QUALITY_INVALID" in result["blockers"]

    result = _derive(_evidence(pace={"home": 100.0, "away": 0.0}))
    assert "NBA_CONTEXT_AWAY_PACE_INVALID" in result["blockers"]

    result = _derive(_evidence(travel={"home": {"rest_days": 2}}))
    assert "NBA_CONTEXT_AWAY_TRAVEL_INVALID" in result["blockers"]


def test_receipt_digest_is_order_independent_and_evidence_is_frozen():
    package = _evidence()
    first = _derive(package)

    reordered = {key: deepcopy(package[key]) for key in reversed(list(package))}
    second = _derive(reordered)
    assert first["evidence_sha256"] == second["evidence_sha256"]

    package["players"]["home"][0]["expected_minutes"] = 0.0
    assert first["source_evidence"]["players"]["home"][0]["expected_minutes"] == 34.0


def test_stored_receipt_round_trips_and_tampering_is_rejected():
    receipt = _derive()
    verified = verify_nba_pregame_context_receipt(receipt)
    assert verified["status"] == "VERIFIED_RESEARCH_RECEIPT"
    assert verified["blockers"] == []
    assert verified["probability_publishable"] is False
    assert verified["can_execute"] is False

    tampered = deepcopy(receipt)
    tampered["features"]["home"]["lineup_continuity"] = 1.0
    rejected = verify_nba_pregame_context_receipt(tampered)
    assert rejected["status"] == "HELD"
    assert "NBA_CONTEXT_RECEIPT_FEATURE_VECTOR_MISMATCH" in rejected["blockers"]

    forged = deepcopy(receipt)
    forged["source_evidence"]["players"]["away"][0]["player_value"] = 99.0
    rejected = verify_nba_pregame_context_receipt(forged)
    assert rejected["status"] == "HELD"
    assert "NBA_CONTEXT_RECEIPT_EVIDENCE_DIGEST_MISMATCH" in rejected["blockers"]

    missing = verify_nba_pregame_context_receipt(None)
    assert missing["status"] == "HELD"
    assert missing["can_execute"] is False
