"""Guardrails for retrospective CFBD player-form studies without PIT evidence."""
from pathlib import Path

SQL = Path(__file__).with_name("ncaaf_retrospective_prior5_research_v1.sql").read_text(encoding="utf-8").lower()


def test_only_prior_games_can_enter_descriptive_rolling_form():
    assert "rows between 5 preceding and 1 preceding" in SQL
    assert "partition by o.season,o.official_player_id,o.stat_type" in SQL
    assert "order by o.event_start_time,o.official_event_id" in SQL
    assert "w.prior_game_stat_count >= 3" in SQL
    assert "w.latest_prior_game_start < w.event_start_time" in SQL
    assert "latest_prior_result_observed_at" in SQL
    assert "prior_source_was_observed_pregame" in SQL


def test_retroactive_source_cannot_masquerade_as_historical_pregame():
    assert "false::boolean as point_in_time_verified" in SQL
    assert "null::timestamptz as verified_feature_as_of" in SQL
    assert "false::boolean as model_training_eligible" in SQL
    assert "false::boolean as probability_publishable" in SQL
    assert "false::boolean as can_execute" in SQL
    assert "target_source_first_observed_at" in SQL
    assert "prior_five_results_mean_reconstructed" in SQL
    assert "retrospective" in SQL
    assert "never represent a historical outcome" in SQL


def test_private_security_invoker_with_no_public_read():
    assert "with (security_invoker=true, security_barrier=true)" in SQL
    assert "revoke all on wow_prop_history.ncaaf_retrospective_prior5_research_v1" in SQL
    assert "from public, anon, authenticated, service_role" in SQL
    assert "grant select on wow_prop_history.ncaaf_retrospective_prior5_research_v1" in SQL
    assert "to service_role" in SQL
    assert "o.source_provider='cfbd'" in SQL
    assert "o.source_status='normalized_research_only'" in SQL
    assert "o.can_execute=false" in SQL
