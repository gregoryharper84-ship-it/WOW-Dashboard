from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from v17 import nfl_forward_settlement_refresh as settlement
from v17 import nfl_forward_shadow as shadow


class _WriteQuery:
    def __init__(self, rows):
        self.rows = rows
        self.payload = None

    def upsert(self, payload, **kwargs):
        self.payload = list(payload)
        self.rows.extend(self.payload)
        return self

    def execute(self):
        return SimpleNamespace(data=list(self.payload or []))


class _WriteClient:
    def __init__(self):
        self.writes = []

    def table(self, name):
        assert name == "wow_nfl_training_games"
        return _WriteQuery(self.writes)


def test_default_refresh_seasons_covers_postseason_boundary():
    assert settlement.default_refresh_seasons(
        datetime(2027, 1, 15, tzinfo=timezone.utc)
    ) == (2026, 2027)


def test_settlement_refresh_materializes_only_completed_games(monkeypatch, tmp_path):
    csv_path = tmp_path / "games.csv"
    csv_path.write_text(
        "game_id,season,game_type,week,gameday,away_team,away_score,home_team,home_score\n"
        "2026_01_DEN_KC,2026,REG,1,2026-09-14,DEN,24,KC,21\n"
        "2026_02_DET_BUF,2026,REG,2,2026-09-17,DET,,BUF,\n"
        "2025_18_OLD_GAME,2025,REG,18,2026-01-04,AAA,10,BBB,20\n",
        encoding="utf-8",
    )

    capture = SimpleNamespace(
        local_path=str(csv_path),
        content_sha256="a" * 64,
    )

    def fake_capture(db, asset, workdir):
        assert asset.dataset_name == "SCHEDULES"
        assert Path(workdir).exists()
        return capture, "11111111-1111-1111-1111-111111111111"

    monkeypatch.setattr(settlement, "_capture_and_preserve", fake_capture)
    client = _WriteClient()
    result = settlement.refresh_recent_settled_outcomes(client, seasons=(2026,))

    assert result["status"] == "COMPLETED"
    assert result["completed_games_materialized"] == 1
    assert result["rows_upserted"] == 1
    assert result["latest_gameday"] == "2026-09-14"
    assert result["probability_publishable"] is False
    assert result["automatic_certification"] is False
    assert result["automatic_promotion"] is False
    assert result["can_execute"] is False
    assert client.writes[0]["game_id"] == "2026_01_DEN_KC"
    assert client.writes[0]["home_score"] == 21
    assert client.writes[0]["away_score"] == 24


def test_forward_shadow_refreshes_settlements_before_loading_outcomes(monkeypatch):
    calls = []

    def refresh(db):
        calls.append("refresh")
        return {
            "status": "COMPLETED",
            "completed_games_materialized": 1,
            "can_execute": False,
        }

    monkeypatch.setattr(shadow, "load_prediction_rows", lambda db: calls.append("predictions") or [])
    monkeypatch.setattr(shadow, "select_canonical_forward_predictions", lambda rows: calls.append("canonical") or [])
    monkeypatch.setattr(shadow, "load_outcome_rows", lambda db: calls.append("outcomes") or [])
    monkeypatch.setattr(shadow, "grade_forward_predictions", lambda predictions, outcomes: calls.append("grades") or [])
    monkeypatch.setattr(shadow, "persist_new_grades", lambda db, grades: 0)
    monkeypatch.setattr(
        shadow,
        "calibration_health",
        lambda grades, min_forward: {
            "schema_version": shadow.HEALTH_SCHEMA_VERSION,
            "generated_at": "2026-09-17T18:00:00+00:00",
            "graded_n": 0,
            "minimum_forward_required": min_forward,
            "status": "INSUFFICIENT_FORWARD_EVIDENCE",
            "certification_recommendation": "DO_NOT_CERTIFY_YET",
            "blockers": ["FORWARD_GRADED_N_0_LT_100"],
            "can_execute": False,
        },
    )
    monkeypatch.setattr(shadow, "persist_health", lambda db, health: None)

    result = shadow.run_forward_shadow(object(), settlement_refresh_fn=refresh)

    assert calls[:4] == ["refresh", "predictions", "canonical", "outcomes"]
    assert result["settlement_refresh"]["completed_games_materialized"] == 1
    assert result["automatic_certification"] is False
    assert result["automatic_promotion"] is False
    assert result["can_execute"] is False
