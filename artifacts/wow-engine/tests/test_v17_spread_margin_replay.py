from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from v17.spread_margin_challenger import SpreadChallengerUnavailable
from v17.spread_margin_replay import (
    NCAAF_PERSISTED_FEATURE_MODEL_FAMILY,
    NCAAF_PERSISTED_FEATURE_SCHEMA_VERSION,
    adapt_basketball_rows,
    adapt_ncaaf_persisted_rows,
    adapt_ncaaf_rows,
    adapt_nfl_rows,
    load_replay_rows,
)
from v17.team_state_intelligence import FEATURE_FAMILY_VERSION


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


def _ncaaf_games():
    base = datetime(2024, 8, 24, 18, 0, tzinfo=timezone.utc)
    games = []
    for i in range(12):
        home, away = (("A", "B") if i % 2 == 0 else ("B", "A"))
        games.append({
            "training_game_id": f"tg-{i}",
            "official_event_id": f"ncaaf-{i}",
            "season": 2024,
            "event_start_time": (base + timedelta(days=7 * i)).isoformat(),
            "home_team": home,
            "away_team": away,
            "home_points": 24 + (i % 6),
            "away_points": 17 + ((i * 3) % 8),
            "result_source": "fixture",
            "result_source_timestamp": (base + timedelta(days=7 * i, hours=4)).isoformat(),
            "can_execute": False,
        })
    return games


def test_ncaaf_adapter_reconstructs_prior_only_team_state_without_market_inputs():
    rows = adapt_ncaaf_rows(_ncaaf_games(), min_prior_games=2)
    assert rows
    for row in rows:
        assert datetime.fromisoformat(row.feature_as_of) < datetime.fromisoformat(row.event_start_time)
        keys = " ".join(row.features).lower()
        assert "market" not in keys
        assert "spread" not in keys
        assert "moneyline" not in keys
        assert "probability" not in keys


def test_ncaaf_persisted_adapter_is_exactly_equivalent_to_reference_reconstruction():
    games = _ncaaf_games()
    reference = adapt_ncaaf_rows(games, min_prior_games=2)
    persisted = []
    for row in reference:
        index = int(row.event_id.rsplit("-", 1)[1])
        persisted.append({
            "official_event_id": f"NCAAF:{row.event_id}",
            "event_start_time": row.event_start_time,
            "feature_as_of": row.feature_as_of,
            "feature_schema_version": NCAAF_PERSISTED_FEATURE_SCHEMA_VERSION,
            "model_family": NCAAF_PERSISTED_FEATURE_MODEL_FAMILY,
            "features": dict(row.features),
            "source_manifest": {
                "feature_family_version": FEATURE_FAMILY_VERSION,
                "home_prior_events": index,
                "away_prior_events": index,
            },
            "market_features_used": False,
            "can_execute": False,
        })

    fast = adapt_ncaaf_persisted_rows(persisted, games)
    assert fast == reference


def test_ncaaf_persisted_adapter_fails_closed_on_market_feature_violation():
    games = _ncaaf_games()
    reference = adapt_ncaaf_rows(games, min_prior_games=2)
    row = reference[0]
    index = int(row.event_id.rsplit("-", 1)[1])
    persisted = [{
        "official_event_id": f"NCAAF:{row.event_id}",
        "event_start_time": row.event_start_time,
        "feature_as_of": row.feature_as_of,
        "feature_schema_version": NCAAF_PERSISTED_FEATURE_SCHEMA_VERSION,
        "model_family": NCAAF_PERSISTED_FEATURE_MODEL_FAMILY,
        "features": dict(row.features),
        "source_manifest": {
            "feature_family_version": FEATURE_FAMILY_VERSION,
            "home_prior_events": index,
            "away_prior_events": index,
        },
        "market_features_used": True,
        "can_execute": False,
    }]
    with pytest.raises(SpreadChallengerUnavailable) as exc:
        adapt_ncaaf_persisted_rows(persisted, games)
    assert exc.value.code == "SPREAD_REPLAY_PERSISTED_NCAAF_MARKET_FEATURE_VIOLATION"
    assert exc.value.code != "MODEL_UNAVAILABLE"


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


def test_ncaaf_persisted_adapter_collapses_refresh_only_duplicate_rows():
    games = _ncaaf_games()
    reference = adapt_ncaaf_rows(games, min_prior_games=2)
    row = reference[0]
    index = int(row.event_id.rsplit("-", 1)[1])
    base = {
        "official_event_id": f"NCAAF:{row.event_id}",
        "event_start_time": row.event_start_time,
        "feature_as_of": row.feature_as_of,
        "feature_schema_version": NCAAF_PERSISTED_FEATURE_SCHEMA_VERSION,
        "model_family": NCAAF_PERSISTED_FEATURE_MODEL_FAMILY,
        "features": dict(row.features),
        "source_manifest": {
            "program": "LLP_DYNAMIC_TEAM_STATE_CHALLENGER_V1",
            "feature_family_version": FEATURE_FAMILY_VERSION,
            "home_prior_events": index,
            "away_prior_events": index,
            "source_manifest": {
                "source": "CFBD:/games",
                "source_timestamp": "2026-09-15T18:35:53+00:00",
                "official_event_id": row.event_id,
            },
        },
        "market_features_used": False,
        "can_execute": False,
    }
    refresh = {
        **base,
        "source_manifest": {
            **base["source_manifest"],
            "source_manifest": {
                **base["source_manifest"]["source_manifest"],
                "source_timestamp": "2026-10-01T18:35:38+00:00",
            },
        },
    }

    result = adapt_ncaaf_persisted_rows([base, refresh], games)
    assert len(result) == 1
    assert result[0].event_id == row.event_id
    assert result[0].features == row.features


def test_ncaaf_persisted_adapter_rejects_conflicting_duplicate_rows():
    games = _ncaaf_games()
    reference = adapt_ncaaf_rows(games, min_prior_games=2)
    row = reference[0]
    index = int(row.event_id.rsplit("-", 1)[1])
    base = {
        "official_event_id": f"NCAAF:{row.event_id}",
        "event_start_time": row.event_start_time,
        "feature_as_of": row.feature_as_of,
        "feature_schema_version": NCAAF_PERSISTED_FEATURE_SCHEMA_VERSION,
        "model_family": NCAAF_PERSISTED_FEATURE_MODEL_FAMILY,
        "features": dict(row.features),
        "source_manifest": {
            "program": "LLP_DYNAMIC_TEAM_STATE_CHALLENGER_V1",
            "feature_family_version": FEATURE_FAMILY_VERSION,
            "home_prior_events": index,
            "away_prior_events": index,
            "source_manifest": {
                "source": "CFBD:/games",
                "source_timestamp": "2026-09-15T18:35:53+00:00",
                "official_event_id": row.event_id,
            },
        },
        "market_features_used": False,
        "can_execute": False,
    }
    conflicting = {
        **base,
        "features": {**base["features"], next(iter(base["features"])): 999.0},
        "source_manifest": {
            **base["source_manifest"],
            "source_manifest": {
                **base["source_manifest"]["source_manifest"],
                "source_timestamp": "2026-10-01T18:35:38+00:00",
            },
        },
    }

    with pytest.raises(SpreadChallengerUnavailable) as exc:
        adapt_ncaaf_persisted_rows([base, conflicting], games)
    assert exc.value.code == "SPREAD_REPLAY_PERSISTED_NCAAF_EVENT_DUPLICATE"
    assert exc.value.code != "MODEL_UNAVAILABLE"
