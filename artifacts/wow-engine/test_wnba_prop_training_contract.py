from datetime import datetime, timedelta, timezone

import pytest

from wnba_prop_training_contract import (
    WNBAPropTrainingContractError,
    canonical_stat,
    normalize_historical_row,
    training_readiness,
)


def _payload(i=0, player_id="p1"):
    start = datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc) + timedelta(days=i)
    return {
        "sport": "WNBA",
        "event_id": f"WNBA-2026-{i:04d}",
        "game_date": start.date().isoformat(),
        "event_start_time": start.isoformat(),
        "player_id": player_id,
        "player_name": "Test Player",
        "team": "DAL",
        "opponent": "NYL",
        "minutes": 32.5,
        "starter": True,
        "pts": 18,
        "reb": 7,
        "ast": 5,
        "three_pm": 2,
        "source_identity": "OFFICIAL_BOX_SCORE",
        "source_timestamp": (start + timedelta(hours=3)).isoformat(),
        "ingested_at": (start + timedelta(hours=4)).isoformat(),
    }


def test_stat_aliases_are_canonical_and_limited():
    assert canonical_stat("points") == "PTS"
    assert canonical_stat("rebounds") == "REB"
    assert canonical_stat("assists") == "AST"
    assert canonical_stat("threes made") == "3PM"
    with pytest.raises(WNBAPropTrainingContractError) as exc:
        canonical_stat("blocks")
    assert exc.value.code == "WNBA_TRAINING_STAT_UNSUPPORTED"


def test_historical_row_is_auditable_and_non_executable():
    row = normalize_historical_row(_payload())
    assert row.stat_value("points") == 18
    assert row.stat_value("REB") == 7
    assert row.can_execute is False
    assert row.source_identity == "OFFICIAL_BOX_SCORE"


def test_source_must_be_post_event_and_ingest_post_source():
    payload = _payload()
    payload["source_timestamp"] = (datetime.fromisoformat(payload["event_start_time"]) - timedelta(minutes=1)).isoformat()
    with pytest.raises(WNBAPropTrainingContractError) as exc:
        normalize_historical_row(payload)
    assert exc.value.code == "WNBA_TRAINING_SOURCE_PRE_RESULT"


def test_empty_readiness_fails_closed_without_runtime_promotion():
    result = training_readiness([], "PTS")
    assert result["training_status"] == "TRAINING_DATA_UNAVAILABLE"
    assert result["runtime_model_status"] == "MODEL_UNAVAILABLE"
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False
    assert "WNBA_TRAINING_ROWS_BELOW_MINIMUM" in result["blockers"]


def test_readiness_can_only_authorize_offline_fit_not_runtime_model():
    rows = [normalize_historical_row(_payload(i, player_id=f"p{i // 20}")) for i in range(500)]
    result = training_readiness(rows, "AST")
    assert result["training_status"] == "READY_FOR_OFFLINE_FIT"
    assert result["runtime_model_status"] == "MODEL_UNAVAILABLE"
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False
