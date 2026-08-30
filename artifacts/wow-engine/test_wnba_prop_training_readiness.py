import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.assess_wnba_prop_training_readiness import assess


def _row(i: int, player_id: str):
    start = datetime(2025, 5, 1, tzinfo=timezone.utc) + timedelta(days=i)
    return {
        "sport": "WNBA",
        "event_id": f"WNBA-{i}",
        "game_date": start.date().isoformat(),
        "event_start_time": start.isoformat(),
        "player_id": player_id,
        "player_name": f"Player {player_id}",
        "team": "DAL",
        "opponent": "NYL",
        "minutes": 30.0,
        "starter": True,
        "pts": 15,
        "reb": 6,
        "ast": 4,
        "three_pm": 1,
        "source_identity": "OFFICIAL_BOX_SCORE",
        "source_timestamp": (start + timedelta(hours=3)).isoformat(),
        "ingested_at": (start + timedelta(hours=4)).isoformat(),
    }


def _write(path: Path, rows):
    path.write_text("\n".join(json.dumps(row) for row in rows))


def test_assessor_fails_closed_on_small_dataset(tmp_path):
    path = tmp_path / "wnba.jsonl"
    _write(path, [_row(i, "p1") for i in range(20)])
    result = assess(path, "PTS")
    assert result["training_status"] == "TRAINING_DATA_UNAVAILABLE"
    assert result["runtime_model_status"] == "MODEL_UNAVAILABLE"
    assert result["artifact_training_status"] == "NOT_ATTEMPTED"
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False


def test_assessor_ready_for_offline_fit_never_promotes_runtime(tmp_path):
    path = tmp_path / "wnba.jsonl"
    rows = [_row(i, f"p{i // 20}") for i in range(500)]
    _write(path, rows)
    result = assess(path, "REB")
    assert result["training_status"] == "READY_FOR_OFFLINE_FIT"
    assert result["runtime_model_status"] == "MODEL_UNAVAILABLE"
    assert result["artifact_training_status"] == "NOT_ATTEMPTED"
    assert result["artifact_registration_status"] == "NOT_ATTEMPTED"
    assert result["artifact_certification_status"] == "NOT_ATTEMPTED"
    assert result["can_execute"] is False


def test_any_rejected_row_blocks_fit_readiness(tmp_path):
    path = tmp_path / "wnba.jsonl"
    rows = [_row(i, f"p{i // 20}") for i in range(500)]
    rows.append({"sport": "WNBA", "bad": True})
    _write(path, rows)
    result = assess(path, "AST")
    assert result["rejected_row_n"] == 1
    assert result["training_status"] == "TRAINING_DATA_UNAVAILABLE"
    assert "WNBA_TRAINING_ROWS_REJECTED" in result["blockers"]
    assert result["runtime_model_status"] == "MODEL_UNAVAILABLE"
