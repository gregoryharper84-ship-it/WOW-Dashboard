from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import v17.fantasy_score_candidate_persistence_bridge as persistence
import v17.fantasy_score_pick_request_bridge as pick_bridge
import v17.mlb_pitcher_fantasy_score_hydration as hydration


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, rows):
        self.rows = rows

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def execute(self):
        return _Result(self.rows)


class _Db:
    def __init__(self, rows):
        self.rows = rows

    def table(self, _name):
        return _Query(self.rows)


def _candidate_artifact_row():
    return {
        "artifact_id": "00000000-0000-0000-0000-000000000001",
        "model_family": "MLB_PITCHER_FANTASY_SCORE_EMPIRICAL_RESIDUAL_V1",
        "model_artifact_version": "candidate-v1",
        "specialist_version": "wow.mlb-pitcher-fantasy-score-expert@1",
        "certification_id": "CANDIDATE-NOT-CERTIFIED",
        "lifecycle_state": "CANDIDATE",
        "promoted": False,
        "active": False,
        "probability_publishable": False,
        "can_execute": False,
        "candidate_research_active": True,
    }


def test_pick_request_preflight_allows_only_nonpublishable_research_candidate():
    market_api = SimpleNamespace(prod=SimpleNamespace(get_client=lambda: _Db([_candidate_artifact_row()])))
    original = {"ok": False, "code": "PROP_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND"}

    result = pick_bridge.research_candidate_preflight(
        market_api,
        "MLB",
        "PITCHER_FANTASY_SCORE",
        original,
    )

    assert result is not None
    assert result["preflight_compatibility_mode"] == "FANTASY_SCORE_RESEARCH_EVIDENCE_ONLY"
    assert result["actual_artifact_lifecycle"] == "CANDIDATE"
    assert result["actual_certification_status"] == "CANDIDATE_ONLY"
    assert result["probability_publishable"] is False
    assert result["rank_eligible"] is False
    assert result["can_execute"] is False


def test_pick_request_preflight_refuses_promoted_or_publishable_candidate():
    row = _candidate_artifact_row()
    row["probability_publishable"] = True
    market_api = SimpleNamespace(prod=SimpleNamespace(get_client=lambda: _Db([row])))

    assert pick_bridge.research_candidate_preflight(
        market_api,
        "MLB",
        "PITCHER_FANTASY_SCORE",
        {"ok": False},
    ) is None


def test_candidate_outcome_is_typed_calibration_hold_not_model_unavailable():
    outcome = pick_bridge.research_candidate_outcome(
        row_key="row-1",
        scored={
            "candidate_model_output": {
                "probability_publishable": False,
                "rank_eligible": False,
                "blockers": ["FANTASY_SCORE_CANDIDATE_UNCALIBRATED"],
            },
            "forward_evidence": {"status": "CAPTURED_FORWARD"},
        },
        snapshot_id="00000000-0000-0000-0000-000000000002",
        fingerprint="abc",
        acquisition={"status": "PASS"},
    )

    assert outcome is not None
    assert outcome["terminal_status"] == "HELD"
    assert outcome["code"] == "CALIBRATION_BLOCKED_NO_PUBLISH"
    assert outcome["model_evaluated"] is True
    assert outcome["model_supported"] is True
    assert outcome["rank_eligible"] is False
    assert outcome["probability_publishable"] is False
    assert outcome["can_execute"] is False


def test_hydrator_builds_exact_pitcher_fantasy_score_history(monkeypatch):
    now = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)
    event_start = now + timedelta(hours=6)

    monkeypatch.setattr(hydration, "_resolve_player_id", lambda *args, **kwargs: (123, "Test Pitcher"))
    monkeypatch.setattr(
        hydration,
        "_schedule_context",
        lambda *args, **kwargs: {
            "starter_status": "STARTER_PROBABLE_OFFICIAL_SCHEDULE",
            "team": "TST",
            "opponent": "OPP",
            "venue": "Test Park",
            "official_game_pk": 99,
            "official_game_date": event_start.isoformat(),
            "schedule_status": "Scheduled",
        },
    )

    splits = []
    for index in range(10):
        game_date = (now.date() - timedelta(days=index + 1)).isoformat()
        splits.append(
            (
                2026,
                {
                    "date": game_date,
                    "opponent": {"abbreviation": "OPP"},
                    "stat": {
                        "gamesStarted": 1,
                        "inningsPitched": "6.0",
                        "strikeOuts": 5,
                        "earnedRuns": 2,
                        "wins": 1,
                        "battersFaced": 24,
                        "baseOnBalls": 2,
                    },
                },
            )
        )
    monkeypatch.setattr(
        hydration,
        "fetch_cross_season_pitching_splits",
        lambda *args, **kwargs: (splits, [2026]),
    )

    evidence = hydration.hydrate_mlb_pitcher_fantasy_score_evidence(
        player="Test Pitcher",
        event_start_time=event_start.isoformat(),
        now=now,
    )

    # 6*W + 3*K - 3*ER + OUT + 4*QS = 6 + 15 - 6 + 18 + 4 = 37
    assert evidence["game_log"] == [37.0] * 10
    assert evidence["box_score_log"][0]["quality_starts"] == 1
    assert evidence["box_score_log"][0]["wins"] == 1
    assert evidence["opportunity_ledger"]["scoring_profile_id"] == hydration.SCORING_PROFILE_ID
    assert evidence["opportunity_ledger"]["status"] == "READY"


def test_candidate_scoring_persists_forward_prediction(monkeypatch):
    snapshot_id = "00000000-0000-0000-0000-000000000003"
    snapshot = {
        "source_snapshot_id": snapshot_id,
        "captured_at": "2026-09-16T12:00:00+00:00",
        "event_id": "MLB:99",
        "event_start_time": "2026-09-16T18:00:00+00:00",
        "sport": "MLB",
        "player": "Test Pitcher",
        "stat_type": "PITCHER_FANTASY_SCORE",
        "line": 30.5,
        "hydration_status": "PASS",
        "blockers": [],
    }
    db = _Db([snapshot])
    market_api = SimpleNamespace(prod=SimpleNamespace(get_client=lambda: db))
    req = SimpleNamespace(
        sport="MLB",
        stat_type="PITCHER_FANTASY_SCORE",
        direction="MORE",
        source_snapshot_id=snapshot_id,
    )

    monkeypatch.setattr(
        persistence,
        "_score_fantasy_candidate_research",
        lambda *args, **kwargs: {
            "candidate_model_output": {
                "probability_publishable": False,
                "rank_eligible": False,
            },
            "backend_traversal": {},
        },
    )
    monkeypatch.setattr(
        persistence,
        "_build_prediction_payload",
        lambda **kwargs: (
            {
                "prediction_id": "pred-1",
                "evidence_source_kind": "IMMUTABLE_PREGAME_SETTLED",
                "calibration_status": "CANDIDATE_UNCALIBRATED",
            },
            [],
        ),
    )
    persisted = []
    monkeypatch.setattr(persistence, "_persist_prediction", lambda _db, payload: persisted.append(payload))

    scored = persistence.score_fantasy_candidate_research(
        market_api,
        req,
        model_identity="WOW_BETTING_ENGINE",
    )

    assert persisted == [
        {
            "prediction_id": "pred-1",
            "evidence_source_kind": "IMMUTABLE_PREGAME_SETTLED",
            "calibration_status": "CANDIDATE_UNCALIBRATED",
        }
    ]
    assert scored["backend_traversal"]["prediction_ledger_write"] == "PASS"
    assert scored["forward_evidence"]["status"] == "CAPTURED_FORWARD"
    assert scored["probability_publishable"] is not True
