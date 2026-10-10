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
    _assert_ncaaf_persisted_feature_freshness,
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



def test_ncaaf_freshness_ignores_newest_settled_game_when_not_feature_eligible():
    games = _ncaaf_games()
    rows = adapt_ncaaf_rows(games, min_prior_games=2)
    latest = max(datetime.fromisoformat(row.event_start_time) for row in rows)
    games.append({
        "training_game_id": "tg-ineligible-new",
        "official_event_id": "ncaaf-ineligible-new",
        "season": 2024,
        "event_start_time": (latest + timedelta(days=7)).isoformat(),
        "home_team": "NEW_A",
        "away_team": "NEW_B",
        "home_points": 21,
        "away_points": 17,
        "result_source": "fixture",
        "result_source_timestamp": (latest + timedelta(days=7, hours=4)).isoformat(),
        "can_execute": False,
    })

    _assert_ncaaf_persisted_feature_freshness(rows, games, min_prior_games=2)


def test_ncaaf_freshness_blocks_when_latest_feature_eligible_row_is_missing():
    games = _ncaaf_games()
    rows = adapt_ncaaf_rows(games, min_prior_games=2)
    assert len(rows) > 1

    with pytest.raises(SpreadChallengerUnavailable) as exc:
        _assert_ncaaf_persisted_feature_freshness(rows[:-1], games, min_prior_games=2)

    assert exc.value.code == "SPREAD_REPLAY_PERSISTED_NCAAF_FEATURES_STALE"
    assert exc.value.code != "MODEL_UNAVAILABLE"



def test_ncaaf_shadow_reference_features_match_persisted_team_state_builder():
    """Guard candidate-maintenance/replay feature parity at the regime cutoff.

    The NCAAF challenger maintenance corpus is built at 13 expected games.
    A spread reference silently configured for 12 changes late-season regime
    features while retaining the same nominal feature family/version.
    """
    from v17.spread_margin_challenger import SPORT_CONFIG
    from v17.spread_margin_replay import _ncaaf_events_from_games
    from v17.team_state_challenger_training import build_dynamic_binary_rows
    from v17.team_state_intelligence import NCAAF_EXPECTED_SEASON_GAMES

    assert NCAAF_EXPECTED_SEASON_GAMES == 13
    assert SPORT_CONFIG["NCAAF"]["expected_season_games"] == NCAAF_EXPECTED_SEASON_GAMES
    games = _ncaaf_games()
    events = _ncaaf_events_from_games(games)
    candidate_rows, metadata, names = build_dynamic_binary_rows(
        events, expected_season_games=NCAAF_EXPECTED_SEASON_GAMES, min_prior_games=2,
    )
    spread_rows = adapt_ncaaf_rows(games, min_prior_games=2)
    assert len(candidate_rows) == len(spread_rows) > 0
    assert {row.event_id for row in candidate_rows} == {row.event_id for row in spread_rows}
    candidate_by_event = {row.event_id: row for row in candidate_rows}
    for spread in spread_rows:
        candidate = candidate_by_event[spread.event_id]
        assert spread.features == candidate.features
        assert spread.feature_as_of == candidate.feature_as_of
        assert spread.event_start_time == candidate.event_start_time
        assert "spread" not in " ".join(spread.features).lower()
        assert "moneyline" not in " ".join(spread.features).lower()

    # The regime uses season_games_prior + 1 (the upcoming game index).
    # Game nine has eight prior results: 9/12 is LATE, 9/13 is MID.
    ninth_game = next(row for row in spread_rows if row.event_id == "ncaaf-8")
    assert ninth_game.features["home_season_regime_mid"] == 1.0
    assert ninth_game.features["away_season_regime_mid"] == 1.0

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
