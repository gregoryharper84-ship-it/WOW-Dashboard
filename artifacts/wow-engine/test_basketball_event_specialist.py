from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).parent
MIGRATION = ROOT / "migrations" / "20260913_nba_wnba_event_specialist_backbone.sql"
MODULE = ROOT / "basketball_event_specialist.py"

spec = importlib.util.spec_from_file_location("basketball_event_specialist", MODULE)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(mod)


def test_migration_creates_independent_basketball_specialists_fail_closed():
    sql = MIGRATION.read_text(encoding="utf-8")
    lower = sql.lower()
    for sport in ("nba", "wnba"):
        assert f"wow_{sport}_source_snapshots" in lower
        assert f"wow_{sport}_training_games" in lower
        assert f"wow_{sport}_pregame_feature_rows" in lower
        assert f"wow_{sport}_event_fitted_model_artifacts" in lower
        assert f"wow_{sport}_event_readiness" in lower
    assert "wow.nba-game-win-probability-expert" in sql
    assert "wow.wnba-game-win-probability-expert" in sql
    assert "WOW_NBA_EVENT_FITTED_MODEL_V1" in sql
    assert "WOW_WNBA_EVENT_FITTED_MODEL_V1" in sql
    assert "'NBA_EVENT_PROBABILITY','UNAVAILABLE'" in sql
    assert "'WNBA_EVENT_PROBABILITY','UNAVAILABLE'" in sql
    assert "can_execute=false" in lower
    # Backbone migrations never invent fitted artifacts or certify capability.
    assert "insert into public.wow_nba_event_fitted_model_artifacts" not in lower
    assert "insert into public.wow_wnba_event_fitted_model_artifacts" not in lower
    assert "capability_status='available'" not in lower


def test_artifact_identity_is_sport_specific():
    nba = mod.SPORTS["NBA"]
    wnba = mod.SPORTS["WNBA"]
    assert nba["provider_identity"] == "WOW_NBA_EVENT_FITTED_MODEL_V1"
    assert wnba["provider_identity"] == "WOW_WNBA_EVENT_FITTED_MODEL_V1"
    assert nba["artifacts_table"] != wnba["artifacts_table"]
    assert nba["features_table"] != wnba["features_table"]
    assert nba["model_family"].startswith("NBA_")
    assert wnba["model_family"].startswith("WNBA_")


def test_scoreboard_urls_use_sport_specific_espn_league_and_no_odds_source():
    from datetime import date
    nba = mod.scoreboard_url("NBA", date(2026, 1, 1), date(2026, 1, 31))
    wnba = mod.scoreboard_url("WNBA", date(2026, 5, 1), date(2026, 5, 31))
    assert "/basketball/nba/scoreboard" in nba
    assert "/basketball/wnba/scoreboard" in wnba
    assert "api.the-odds-api.com" not in MODULE.read_text(encoding="utf-8")
    assert "sportsbook" not in MODULE.read_text(encoding="utf-8").lower()


def test_completed_game_parser_accepts_final_game_and_preserves_source_identity():
    payload = {
        "events": [{
            "id": "401-test",
            "date": "2026-06-01T00:00:00Z",
            "competitions": [{
                "status": {"type": {"completed": True}},
                "competitors": [
                    {"homeAway": "home", "score": "91", "team": {"abbreviation": "DAL"}},
                    {"homeAway": "away", "score": "87", "team": {"abbreviation": "NYL"}},
                ],
            }],
        }]
    }
    rows = mod._parse_completed_games(payload, "snapshot-1", 2026)
    assert len(rows) == 1
    row = rows[0]
    assert row["event_id"] == "401-test"
    assert row["home_team"] == "DAL"
    assert row["away_team"] == "NYL"
    assert row["home_win"] is True
    assert row["source_snapshot_id"] == "snapshot-1"
    assert len(row["row_inputs_hash"]) == 64
    assert row["probability_publishable"] is False
    assert row["can_execute"] is False


def test_completed_game_parser_rejects_unfinished_tied_or_identity_incomplete_rows():
    payload = {"events": [
        {"id": "unfinished", "date": "2026-01-01T00:00:00Z", "competitions": [{"status": {"type": {"completed": False}}, "competitors": []}]},
        {"id": "tie", "date": "2026-01-02T00:00:00Z", "competitions": [{"status": {"type": {"completed": True}}, "competitors": [
            {"homeAway": "home", "score": "100", "team": {"abbreviation": "AAA"}},
            {"homeAway": "away", "score": "100", "team": {"abbreviation": "BBB"}},
        ]}]},
        {"id": "missing-team", "date": "2026-01-03T00:00:00Z", "competitions": [{"status": {"type": {"completed": True}}, "competitors": [
            {"homeAway": "home", "score": "101", "team": {"abbreviation": ""}},
            {"homeAway": "away", "score": "99", "team": {"abbreviation": "BBB"}},
        ]}]},
    ]}
    assert mod._parse_completed_games(payload, "snapshot-1", 2026) == []


def test_candidate_trainer_never_contains_promotion_or_execution_mutation():
    source = MODULE.read_text(encoding="utf-8")
    normalized = " ".join(source.split()).lower()
    assert '"lifecycle_state": "candidate"' in normalized
    assert '"active": false' in normalized
    assert '"promoted": false' in normalized
    assert '"probability_publishable": false' in normalized
    assert '"can_execute": false' in normalized
    assert "prospective_certified" not in normalized
    assert '"champion"' not in normalized
