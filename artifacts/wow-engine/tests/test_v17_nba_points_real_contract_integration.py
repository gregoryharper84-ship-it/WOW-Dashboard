"""Real V17 validator/snapshot seam integration for the research-only NBA POINTS bridge.

These are synthetic source fixtures, never live NBA evidence or model predictions.
Unlike the unit tests, these invoke the actual PickRequestRow/RawPropEvidence,
_validate_evidence and _snapshot_payload implementations without injected fakes.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from v17.nba_points_snapshot_bridge import NBAPointsSourceError, build_nba_points_snapshot


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _real_contract_input() -> dict:
    now = datetime.now(timezone.utc)
    game = now + timedelta(days=3)
    capture = now - timedelta(minutes=2)
    roster_ts = now - timedelta(minutes=5)
    quote_ts = now - timedelta(minutes=4)
    source_ts = now - timedelta(minutes=7)
    game_id = "espn-synthetic-regression-fixture"
    player_id = "9999999"
    points = [10 + i for i in range(10)]
    boxscores = [
        {
            "event_id": f"nba-prior-synthetic-{i}",
            "event_start_time": _iso(now - timedelta(days=i + 2)),
            "player_id": player_id,
            "points": points[i],
        }
        for i in range(10)
    ]
    return {
        "now": now,
        "target": {
            "event_start_time": _iso(game),
            "home_team_alias": "espn-17",
            "away_team_alias": "espn-22",
            "team_id": "espn-17",
            "opponent_id": "espn-22",
            "player_id": player_id,
            "player": "Synthetic Test Player",
            "period": "FULL_GAME",
            "line": 21.5,
            "direction": "MORE",
        },
        "roster": {
            "event_id": game_id,
            "player_id": player_id,
            "team_id": "espn-17",
            "player": "Synthetic Test Player",
            "availability": "ACTIVE",
            "captured_at": _iso(roster_ts),
        },
        "line_observation": {
            "provider": "SYNTHETIC_TEST_CAPTURE",
            "line_type": "EXACT_BOARD_LINE",
            "event_id": game_id,
            "event_start_time": _iso(game),
            "player_id": player_id,
            "stat_type": "POINTS",
            "period": "FULL_GAME",
            "direction": "MORE",
            "line": 21.5,
            "captured_at": _iso(quote_ts),
        },
        "evidence": {
            "captured_at": _iso(capture),
            "game_log": points,
            "box_score_log": boxscores,
            "role_status": {"status": "ACTIVE"},
            "role_timestamp": _iso(roster_ts),
            "opportunity_ledger": {"status": "PASS"},
            "source_timestamps": {"SYNTHETIC_NBA_L10": _iso(source_ts)},
            "rate_provenance": "SYNTHETIC_V17_INTEGRATION_FIXTURE",
            "evidence_version": "PROP_EVIDENCE_V1",
        },
    }


def _fixture_identity(**_kwargs) -> dict:
    return {
        "event_id": "espn-synthetic-regression-fixture",
        "identity_resolution": "SPORTSDATAVERSE_SCHEDULE_EXACT_TEAM_DATE_MATCH",
        "home_team_id": "espn-17",
        "away_team_id": "espn-22",
    }


def _actual_bridge(inputs: dict) -> dict:
    return build_nba_points_snapshot(
        **inputs, resolve_identity=_fixture_identity
    )


def test_real_v17_validator_and_snapshot_factory_produce_immutable_shape():
    data = _real_contract_input()
    unchanged = deepcopy(data)
    first = _actual_bridge(data)
    second = _actual_bridge(data)
    assert data == unchanged
    assert first["status"] == "SOURCE_VALIDATED_SNAPSHOT_NOT_PERSISTED"
    assert first["source_snapshot_id"] == second["source_snapshot_id"]
    assert first["evidence_fingerprint"] == second["evidence_fingerprint"]
    snapshot = first["snapshot"]
    assert snapshot["source_snapshot_id"] == first["source_snapshot_id"]
    assert snapshot["sport"] == "NBA"
    assert snapshot["stat_type"] == "POINTS"
    assert snapshot["player"] == "Synthetic Test Player"
    assert snapshot["hydration_status"] == "PASS"
    assert snapshot["line"] == 21.5
    assert snapshot["event_id"] == "espn-synthetic-regression-fixture"
    assert snapshot["can_execute"] is False
    assert first["write_performed"] is False
    assert first["rank_eligible"] is False
    assert first["probability_publishable"] is False
    for field in ("model_probability", "calibrated_probability", "calibrated_lower_bound"):
        assert field not in first


def test_actual_v17_snapshot_fingerprint_changes_with_exact_line():
    data = _real_contract_input()
    original = _actual_bridge(data)
    data["target"]["line"] = 22.5
    data["line_observation"]["line"] = 22.5
    changed = _actual_bridge(data)
    assert original["source_snapshot_id"] != changed["source_snapshot_id"]
    assert original["evidence_fingerprint"] != changed["evidence_fingerprint"]
    assert changed["probability_publishable"] is False


def test_actual_v17_snapshot_fingerprint_changes_with_historical_points():
    data = _real_contract_input()
    original = _actual_bridge(data)
    data["evidence"]["game_log"][0] += 1
    data["evidence"]["box_score_log"][0]["points"] += 1
    changed = _actual_bridge(data)
    assert original["source_snapshot_id"] != changed["source_snapshot_id"]
    assert changed["write_performed"] is False


def test_actual_v17_evidence_contract_rejects_bad_opportunity_ledger():
    data = _real_contract_input()
    data["evidence"]["opportunity_ledger"]["status"] = "UNKNOWN"
    with pytest.raises(NBAPointsSourceError) as exc:
        _actual_bridge(data)
    assert exc.value.code == "NBA_POINTS_CANONICAL_EVIDENCE_REJECTED"


def test_actual_v17_evidence_contract_rejects_source_changed_after_capture():
    data = _real_contract_input()
    data["evidence"]["source_timestamps"]["SYNTHETIC_NBA_L10"] = _iso(
        data["now"] - timedelta(minutes=1)
    )
    with pytest.raises(NBAPointsSourceError) as exc:
        _actual_bridge(data)
    assert exc.value.code == "NBA_POINTS_SOURCE_TIMESTAMP_INVALID"
