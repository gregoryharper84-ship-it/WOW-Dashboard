from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pick_request_runtime_core as core
from v17.exact_board_reconciliation import (
    EXACT_BOARD_IDENTITY_MISMATCH,
    enforce_exact_board_identity,
)
from v17.fantasy_score_pick_capture import capture_fantasy_score_candidate_evidence
import v17.mlb_pitcher_fantasy_score_hydration as fs_hydration


def test_exact_board_line_mismatch_blocks_batch_without_mutating_probability():
    source = [{
        "row_key": "wheeler",
        "event_id": "MLB:1",
        "sport": "MLB",
        "player": "Zack Wheeler",
        "stat_type": "PITCHER_STRIKEOUTS",
        "line": 6.5,
        "direction": "MORE",
    }]
    response = {
        "ok": True,
        "run_controller_status": "COMPLETE",
        "reconciliation_pass": True,
        "rows": [{
            "row_key": "wheeler",
            "terminal_status": "COMPLETED",
            "model_evaluated": True,
            "result": {
                "prediction": {
                    "event_id": "MLB:1",
                    "player": "Zack Wheeler",
                    "sport": "MLB",
                    "stat_type": "PITCHER_STRIKEOUTS",
                    "line": 4.5,
                    "direction": "MORE",
                    "calibrated_probability": 0.62,
                    "calibrated_probability_lower_bound": 0.55,
                }
            },
            "can_execute": False,
        }],
    }
    out = enforce_exact_board_identity(response, source)
    assert out["ok"] is False
    assert out["run_controller_status"] == "BLOCKED"
    assert out["completion_blocker"] == EXACT_BOARD_IDENTITY_MISMATCH
    assert out["exact_board_identity_reconciliation"]["mismatch_count"] == 1
    assert out["exact_board_identity_reconciliation"]["mismatches"][0]["fields"] == ["line"]
    assert out["rows"][0]["result"]["prediction"]["calibrated_probability"] == 0.62
    assert out["can_execute"] is False if "can_execute" in out else True


def test_exact_board_identity_echoes_supplied_line_when_package_has_no_identity():
    source = [{
        "row_key": "snell",
        "event_id": "MLB:2",
        "sport": "MLB",
        "player": "Blake Snell",
        "stat_type": "K",
        "line": 4.5,
        "direction": "MORE",
    }]
    response = {"rows": [{"row_key": "snell", "model_evaluated": True, "result": {"prediction": {}}}]}
    out = enforce_exact_board_identity(response, source)
    assert out["rows"][0]["request_identity"]["line"] == 4.5
    assert out["rows"][0]["request_identity"]["stat_type"] == "PITCHER_STRIKEOUTS"
    assert out["exact_board_identity_reconciliation"]["identity_unverifiable_row_ids"] == ["snell"]
    assert out["exact_board_identity_reconciliation"]["balanced"] is True


class _Mutation:
    def __init__(self, sink, payload):
        self.sink = sink
        self.payload = payload

    def execute(self):
        self.sink.append(self.payload)
        return SimpleNamespace(data=[self.payload])


class _Table:
    def __init__(self, sink):
        self.sink = sink

    def upsert(self, payload, on_conflict=None):
        assert on_conflict == "source_snapshot_id"
        return _Mutation(self.sink, payload)


class _Client:
    def __init__(self, sink):
        self.sink = sink

    def table(self, name):
        assert name == "wow_prop_evidence_snapshots"
        return _Table(self.sink)


class _Market:
    def __init__(self, sink):
        self.prod = SimpleNamespace(get_client=lambda: _Client(sink))

    def _prop_route_artifact(self, sport, stat):
        assert (sport, stat) == ("MLB", "PITCHER_FANTASY_SCORE")
        return {"ok": False, "code": "PROP_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND"}


def _raw_evidence():
    now = datetime.now(timezone.utc)
    return {
        "captured_at": now.isoformat(),
        "game_log": [20.0 + i for i in range(10)],
        "box_score_log": [{"fantasy_score": 20.0 + i, "outs": 18, "so": 5, "er": 2} for i in range(10)],
        "role_status": {"status": "STARTER_PROBABLE_OFFICIAL_SCHEDULE", "role": "STARTING_PITCHER"},
        "role_timestamp": now.isoformat(),
        "opportunity_ledger": {"status": "READY"},
        "source_timestamps": {"MLB_STATS_API_PITCHING_GAME_LOG": now.isoformat()},
        "evidence_version": "PROP_EVIDENCE_V1",
        "rate_provenance": "official test fixture",
    }


def test_fantasy_candidate_freezes_exact_supplied_line_before_certification():
    persisted = []
    batch = core.PickRequestBatch(rows=[core.PickRequestRow(
        row_key="fs",
        event_id="MLB:99",
        event_start_time=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        sport="MLB",
        player="Test Pitcher",
        stat_type="PITCHER_FANTASY_SCORE",
        line=35.5,
        direction="MORE",
        source_type="PASTED_BOARD",
    )])

    receipts = capture_fantasy_score_candidate_evidence(
        batch,
        market_api=_Market(persisted),
        hydrate=lambda **_kwargs: _raw_evidence(),
    )
    assert receipts["fs"]["status"] == "CAPTURED"
    assert receipts["fs"]["probability_publishable"] is False
    assert receipts["fs"]["rank_eligible"] is False
    assert len(persisted) == 1
    assert persisted[0]["stat_type"] == "PITCHER_FANTASY_SCORE"
    assert persisted[0]["line"] == 35.5
    assert persisted[0]["hydration_status"] == "PASS"
    assert persisted[0]["can_execute"] is False


def test_mlb_pitcher_fantasy_hydrator_uses_verified_component_formula(monkeypatch):
    now = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)
    event_start = now + timedelta(hours=8)
    monkeypatch.setattr(fs_hydration, "_resolve_player_id", lambda *_args, **_kwargs: (123, "Test Pitcher"))
    monkeypatch.setattr(fs_hydration, "_schedule_context", lambda *_args, **_kwargs: {
        "starter_status": "STARTER_PROBABLE_OFFICIAL_SCHEDULE",
        "team": "TST",
        "opponent": "OPP",
        "venue": "Test Park",
        "official_game_pk": 999,
        "official_game_date": event_start.isoformat(),
        "schedule_status": "Scheduled",
    })
    splits = []
    for i in range(10):
        splits.append((2026, {
            "date": f"2026-09-{16-i:02d}",
            "opponent": {"abbreviation": "OPP"},
            "stat": {
                "gamesStarted": 1,
                "inningsPitched": "6.0",
                "battersFaced": 24,
                "strikeOuts": 5,
                "baseOnBalls": 2,
                "earnedRuns": 2,
                "wins": 1,
            },
        }))
    monkeypatch.setattr(fs_hydration, "fetch_cross_season_pitching_splits", lambda *_args, **_kwargs: (splits, [2026]))

    evidence = fs_hydration.hydrate_mlb_pitcher_fantasy_score_evidence(
        player="Test Pitcher",
        event_start_time=event_start.isoformat(),
        now=now,
    )
    # 6W + 4QS + 3*5K + 18 outs - 3*2ER = 37.
    assert evidence["game_log"] == [37.0] * 10
    assert evidence["box_score_log"][0]["quality_starts"] == 1
    assert evidence["box_score_log"][0]["wins"] == 1
    assert evidence["opportunity_ledger"]["scoring_profile_id"] == fs_hydration.SCORING_PROFILE_ID
