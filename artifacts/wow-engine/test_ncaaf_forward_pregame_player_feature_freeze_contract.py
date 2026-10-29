"""NCAAF 2026 forward player research freeze: no leak, promotion or market shortcut."""
from pathlib import Path

SQL = Path(__file__).with_name(
    "ncaaf_forward_pregame_player_feature_freeze_v1.sql"
).read_text(encoding="utf-8").lower()


def test_genuine_database_time_and_future_only():
    for item in (
        "capture_time timestamptz := pg_catalog.now()",
        "capture_time>=s.kickoff",
        "s.retrieved_at<capture_time",
        "o.observed_at<capture_time",
        "o.event_start_time<s.kickoff",
        "o.official_event_id<>p_event_id",
        "ncaaf_forward_feature_event_already_in_results",
        "ncaaf_forward_feature_upcoming_identity_unverified",
    ):
        assert item in SQL


def test_immutable_stat_provenance_and_four_to_five_prior_games():
    for item in (
        "latest_prior_result_observed_at",
        "source_payload_sha256",
        "schedule_source_snapshot_id",
        "schedule_payload_sha256",
        "prior_results_sha256",
        "extensions.digest",
        "having count(*) between 4 and 5",
        "prior_rank<=5",
        "partition by o.official_player_id,o.team,o.stat_type",
    ):
        assert item in SQL


def test_sport_exact_identity_and_nonpromotion():
    for route in (
        "passing_yards", "completions", "pass_attempts", "rushing_yards",
        "rush_attempts", "receiving_yards", "receptions",
    ):
        assert f"'{route}'" in SQL
    for field in (
        "current_roster_verified", "exact_market_line_verified",
        "model_training_eligible", "probability_publishable", "can_execute",
    ):
        assert f"{field}=false" in SQL
    assert "source_rights_scope='cfbd_v17_approved_sporting_research'" in SQL


def test_only_service_role_may_freeze_research():
    assert "enable row level security" in SQL
    assert "security invoker set search_path=''" in SQL
    assert "revoke all on function wow_prop_history.freeze_ncaaf_future_player_form" in SQL
    assert "from public,anon,authenticated" in SQL
    assert "to service_role" in SQL
    assert "'ncaaf_forward_pit_player_form_research_frozen'" in SQL
    assert "p_stat_type is null" in SQL
