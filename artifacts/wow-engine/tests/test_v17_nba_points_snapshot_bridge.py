"""Source-only NBA POINTS capture bridge, no scored/certified probabilities."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from v17.nba_points_snapshot_bridge import NBAPointsSourceError, build_nba_points_snapshot

NOW = datetime(2026, 10, 22, 19, 0, tzinfo=timezone.utc)


def fixture():
    target = {
        "event_start_time": "2026-10-23T02:00:00Z",
        "home_team_alias": "espn-17", "away_team_alias": "espn-22",
        "team_id": "espn-17", "opponent_id": "espn-22",
        "player_id": "nba-678", "player": "Player Example",
        "period": "FULL_GAME", "line": 21.5, "direction": "MORE",
    }
    roster = {"player_id": "nba-678", "team_id": "espn-17",
              "player": "Player Example", "availability": "ACTIVE",
              "captured_at": "2026-10-22T18:20:00Z"}
    line = {"provider": "EXACT_CAPTURED_BOARD", "line_type": "EXACT_BOARD_LINE",
            "line": "21.50", "captured_at": "2026-10-22T18:25:00Z"}
    logs = [
        {"event_id": f"prior-{i}", "event_start_time": "2026-10-14T02:00:00Z",
         "player_id": "nba-678", "points": 10 + i} for i in range(10)
    ]
    evidence = {
        "captured_at": "2026-10-22T18:30:00Z",
        "game_log": list(range(10, 20)), "box_score_log": logs,
        "role_status": {"status": "ACTIVE"},
        "role_timestamp": "2026-10-22T18:20:00Z",
        "opportunity_ledger": {"status": "PASS"},
        "source_timestamps": {"NBA_GAME_LOG": "2026-10-22T18:15:00Z"},
        "rate_provenance": "OBSERVED_PRIOR_NBA_GAME_POINTS",
        "evidence_version": "PROP_EVIDENCE_V1",
    }
    return target, roster, line, evidence


def canonical(**kwargs):
    return {
        "event_id": "espn-123456",
        "identity_resolution": "SPORTSDATAVERSE_SCHEDULE_EXACT_TEAM_DATE_MATCH",
        "home_team_id": "espn-17", "away_team_id": "espn-22",
    }


class FakeEvidence:
    @classmethod
    def model_validate(cls, raw):
        return SimpleNamespace(**raw)


def fake_row(**kwargs):
    return SimpleNamespace(**kwargs)


def fake_validate(row, stat):
    assert stat == "POINTS" and len(row.evidence.game_log) == 10
    assert row.source_type == "NORMALIZED"
    return {"status": "PASS"}


def fake_snapshot(row, normalized):
    assert normalized["status"] == "PASS"
    return "snapshot-id", "evidence-hash", {
        "event_id": row.event_id, "sport": row.sport,
        "player": row.player, "stat_type": row.stat_type,
        "line": row.line, "hydration_status": "PASS",
        "can_execute": False,
    }


def run(*, target=None, roster=None, line=None, evidence=None,
        resolver=canonical, snapshot_factory=fake_snapshot):
    a, b, c, d = fixture()
    return build_nba_points_snapshot(
        target=target if target is not None else a,
        roster=roster if roster is not None else b,
        line_observation=line if line is not None else c,
        evidence=evidence if evidence is not None else d,
        now=NOW, resolve_identity=resolver,
        validate_evidence=fake_validate, snapshot_factory=snapshot_factory,
        row_factory=fake_row, evidence_factory=FakeEvidence,
    )


def fails(code, **kwargs):
    with pytest.raises(NBAPointsSourceError) as exc:
        run(**kwargs)
    assert exc.value.code == code


def test_full_source_is_validated_but_never_scored_or_persisted():
    a, b, c, d = fixture()
    before = deepcopy((a, b, c, d))
    out = run(target=a, roster=b, line=c, evidence=d)
    assert out["status"] == "SOURCE_VALIDATED_SNAPSHOT_NOT_PERSISTED"
    assert out["source_snapshot_id"] == "snapshot-id"
    assert out["evidence_fingerprint"] == "evidence-hash"
    assert out["snapshot"]["source_snapshot_id"] == "snapshot-id"
    assert out["source_identity"]["canonical_event_id"] == "espn-123456"
    assert out["can_execute"] is False
    assert out["write_performed"] is False
    assert out["probability_publishable"] is False
    assert out["rank_eligible"] is False
    for field in ("raw_model_probability", "calibrated_probability", "calibrated_lower_bound"):
        assert field not in out
    assert (a, b, c, d) == before


def test_less_preserves_exact_captured_line():
    a, b, c, d = fixture()
    a["direction"] = "LESS"
    assert run(target=a)["snapshot"]["line"] == 21.5


@pytest.mark.parametrize("side,code", [
    ("OVER", "NBA_POINTS_DIRECTION_INVALID"),
    ("", "NBA_POINTS_DIRECTION_MISSING"),
    ("BOTH", "NBA_POINTS_DIRECTION_INVALID"),
])
def test_direction_requires_exact_more_or_less(side, code):
    a, _, _, _ = fixture()
    a["direction"] = side
    fails(code, target=a)


@pytest.mark.parametrize("change,code", [
    ("adjacent", "NBA_POINTS_ADJACENT_LINE_NOT_EXACT"),
    ("wrong_line", "NBA_POINTS_EXACT_LINE_MISMATCH"),
    ("line_after_capture", "NBA_POINTS_LINE_TIMESTAMP_INVALID"),
])
def test_market_source_rejects_inexact_or_late_line(change, code):
    _, _, c, _ = fixture()
    if change == "adjacent":
        c["line_type"] = "ADJACENT_BOOK_LINE"
    elif change == "wrong_line":
        c["line"] = 22.5
    else:
        c["captured_at"] = "2026-10-22T18:50:00Z"
    fails(code, line=c)


@pytest.mark.parametrize("change,code", [
    ("player", "NBA_POINTS_ROSTER_PLAYER_MISMATCH"),
    ("name", "NBA_POINTS_ROSTER_PLAYER_MISMATCH"),
    ("inactive", "NBA_POINTS_PLAYER_AVAILABILITY_HOLD"),
    ("late_roster", "NBA_POINTS_ROSTER_TIMESTAMP_INVALID"),
])
def test_roster_player_and_availability_are_bound(change, code):
    _, b, _, _ = fixture()
    if change == "player":
        b["player_id"] = "other"
    elif change == "name":
        b["player"] = "Different Player"
    elif change == "inactive":
        b["availability"] = "OUT"
    else:
        b["captured_at"] = "2026-10-22T19:15:00Z"
    fails(code, roster=b)


def test_wrong_team_cannot_claim_canonical_event():
    a, _, _, _ = fixture()
    a["team_id"] = "espn-33"
    fails("NBA_POINTS_CANONICAL_TEAM_MISMATCH", target=a)


def test_started_game_rejected():
    a, _, _, _ = fixture()
    a["event_start_time"] = "2026-10-22T18:50:00Z"
    fails("NBA_POINTS_EVENT_NOT_PREGAME", target=a)


def test_period_must_be_full_game():
    a, _, _, _ = fixture()
    a["period"] = "FIRST_HALF"
    fails("NBA_POINTS_PERIOD_UNSUPPORTED", target=a)


@pytest.mark.parametrize("change,code", [
    ("missing_row", "NBA_POINTS_L10_EVIDENCE_INCOMPLETE"),
    ("wrong_player", "NBA_POINTS_PRIOR_GAME_PLAYER_MISMATCH"),
    ("duplicate", "NBA_POINTS_PRIOR_GAME_DUPLICATE_OR_TARGET"),
    ("future_game", "NBA_POINTS_PRIOR_GAME_FUTURE_LEAKAGE"),
    ("wrong_points", "NBA_POINTS_GAME_BOX_SCORE_MISMATCH"),
])
def test_prior_game_provenance_is_frozen_and_exact(change, code):
    _, _, _, d = fixture()
    if change == "missing_row":
        d["box_score_log"].pop()
    elif change == "wrong_player":
        d["box_score_log"][0]["player_id"] = "other"
    elif change == "duplicate":
        d["box_score_log"][1]["event_id"] = "prior-0"
    elif change == "future_game":
        d["box_score_log"][0]["event_start_time"] = "2026-10-23T04:00:00Z"
    else:
        d["box_score_log"][0]["points"] = 99
    fails(code, evidence=d)


def test_future_source_timestamp_fails():
    _, _, _, d = fixture()
    d["source_timestamps"]["NBA_GAME_LOG"] = "2026-10-22T20:00:00Z"
    fails("NBA_POINTS_SOURCE_TIMESTAMP_INVALID", evidence=d)


def test_naive_capture_fails():
    _, _, _, d = fixture()
    d["captured_at"] = "2026-10-22T18:30:00"
    fails("NBA_POINTS_CAPTURED_AT_NAIVE", evidence=d)


def test_schedule_alias_is_not_model_authority():
    def unresolved(**kwargs):
        result = canonical()
        result["identity_resolution"] = "PROVIDER_ALIAS_ONLY"
        return result
    fails("NBA_POINTS_CANONICAL_EVENT_UNVERIFIED", resolver=unresolved)


def test_canonical_factory_must_preserve_event_identity():
    def incorrect(row, normalized):
        return "id", "hash", {
            "event_id": "wrong", "stat_type": "POINTS",
            "player": "Player Example", "line": 21.5,
        }
    fails("NBA_POINTS_SNAPSHOT_IDENTITY_MISMATCH", snapshot_factory=incorrect)
