from __future__ import annotations

from datetime import datetime

import pytest

from v17.spread_margin_challenger import SpreadChallengerUnavailable
from v17.spread_margin_replay import adapt_basketball_rows, adapt_nfl_rows, load_replay_rows


def test_nfl_adapter_uses_only_pregame_numeric_features_and_settled_margin():
    features = [{
        "game_id": "2025_01_A_B",
        "gameday": "2025-09-07",
        "feature_schema_version": "NFL_V1",
        "max_prior_gameday": "2025-01-05",
        "features": {"point_diff_edge": 2.5, "win_rate_edge": 0.1, "non_numeric": "ignore"},
        "source_content_sha256s": ["abc"],
        "row_inputs_hash": "rowhash",
        "training_eligible": True,
    }]
    games = [{"game_id": "2025_01_A_B", "gameday": "2025-09-07", "home_score": 27, "away_score": 20}]
    rows = adapt_nfl_rows(features, games)
    assert len(rows) == 1
    row = rows[0]
    assert row.margin == 7
    assert row.features == {"point_diff_edge": 2.5, "win_rate_edge": 0.1}
    assert datetime.fromisoformat(row.feature_as_of) < datetime.fromisoformat(row.event_start_time)


def test_basketball_adapter_uses_feature_payload_and_no_market_fields():
    features = [{
        "game_id": "401",
        "as_of": "2025-01-02T00:00:00+00:00",
        "feature_schema_version": "BASKETBALL_TEAM_EVENT_FEATURES_V2",
        "feature_payload_sha256": "payloadhash",
        "feature_payload": {
            "game_date": "2025-01-02",
            "features": {
                "home_point_diff_prior": 4.2,
                "away_point_diff_prior": -1.1,
                "home_back_to_back": False,
                "away_back_to_back": True,
            },
        },
    }]
    games = [{"game_id": "401", "game_date": "2025-01-02", "home_score": 104, "away_score": 99, "settled": True}]
    rows = adapt_basketball_rows("NBA", features, games)
    assert len(rows) == 1
    row = rows[0]
    assert row.margin == 5
    assert row.features["home_back_to_back"] == 0.0
    assert row.features["away_back_to_back"] == 1.0
    assert "spread" not in " ".join(row.features).lower()
    assert "market" not in " ".join(row.features).lower()
    assert datetime.fromisoformat(row.feature_as_of) < datetime.fromisoformat(row.event_start_time)


def test_ncaaf_replay_is_typed_unavailable_not_generic_model_unavailable():
    class NeverUsedClient:
        pass

    with pytest.raises(SpreadChallengerUnavailable) as exc:
        load_replay_rows(NeverUsedClient(), sport="NCAAF")
    assert exc.value.code == "SPREAD_REPLAY_FEATURES_UNAVAILABLE"


def test_ncaab_replay_is_typed_dataset_unavailable():
    class NeverUsedClient:
        pass

    with pytest.raises(SpreadChallengerUnavailable) as exc:
        load_replay_rows(NeverUsedClient(), sport="NCAAB")
    assert exc.value.code == "SPREAD_REPLAY_DATASET_UNAVAILABLE"


def test_unknown_replay_sport_fails_closed():
    class NeverUsedClient:
        pass

    with pytest.raises(SpreadChallengerUnavailable) as exc:
        load_replay_rows(NeverUsedClient(), sport="CRICKET")
    assert exc.value.code == "SPREAD_REPLAY_SPORT_UNSUPPORTED"
