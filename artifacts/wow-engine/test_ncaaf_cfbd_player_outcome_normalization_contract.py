"""Deterministic SQL contract for private CFBD seven-stat research normalization.

Live DB run receipts are separate from these static fail-closed regression tests.
"""
from pathlib import Path

SQL = Path(__file__).with_name(
    "ncaaf_cfbd_player_outcome_normalization_v1.sql"
).read_text(encoding="utf-8").lower()


def test_only_cfbd_seven_scoped_sporting_stats_are_admitted():
    assert "source_provider='cfbd'" in SQL
    assert "provider='cfbd'" in SQL
    assert "endpoint='/games/players'" in SQL
    for stat in (
        "passing_yards", "completions", "pass_attempts", "rushing_yards",
        "rush_attempts", "receiving_yards", "receptions"
    ):
        assert f"'{stat}'" in SQL
    assert "actual_value between -1000 and 2000" in SQL
    assert "actual_value>=0" in SQL


def test_no_historical_market_price_or_model_probability_authority():
    for field in (
        "historical_closing_line", "historical_odds",
        "historical_book", "historical_market_timestamp",
    ):
        assert f"check ({field} is null)" in SQL
    assert "model_training_eligible=false" in SQL
    assert "probability_publishable=false" in SQL
    assert "can_execute=false" in SQL


def test_private_invoker_and_idempotent_week_atomicity():
    assert "enable row level security" in SQL
    assert "revoke all on table wow_prop_history.ncaaf_player_game_outcomes" in SQL
    assert "from public, anon, authenticated, service_role" in SQL
    assert "security invoker" in SQL
    assert "revoke all on function wow_prop_history.normalize_ncaaf_player_week" in SQL
    assert "grant execute on function wow_prop_history.normalize_ncaaf_player_week" in SQL
    assert "source_snapshot_id,official_event_id,official_player_id,stat_type" in SQL
    assert "on conflict(" in SQL and "do nothing" in SQL
    assert "ncaaf_prop_normalization_count_mismatch" in SQL
    assert "ncaaf_prop_normalization_game_identity_invalid" in SQL


def test_pregame_leakage_and_incorrect_completion_pairs_are_not_silent():
    assert "observed_at > event_start_time" in SQL
    assert "src.retrieved_at <= game.event_start_time" in SQL
    assert "split_part(val,'/',1)::numeric>split_part(val,'/',2)::numeric" in SQL
    assert "ncaaf_prop_normalization_stat_invalid" in SQL
