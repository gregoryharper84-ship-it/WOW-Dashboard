"""Exact contract for observed pregame NCAAF schedule-only research."""
from pathlib import Path

SQL = Path(__file__).with_name("ncaaf_pregame_schedule_research_v1.sql").read_text().lower()

def test_source_is_cfbd_official_schedule_and_immutable_capture():
    for item in (
        "s.provider='cfbd'", "s.endpoint='/games'",
        "s.acquisition_status='available'",
        "s.can_execute=false",
        "g.value->>'completed'='false'",
        "source_captured_at < captured_kickoff",
        "source_payload_sha256 ~ '^[0-9a-f]{64}$'",
        "partition by official_event_id_claimed",
        "order by source_captured_at,source_snapshot_id",
        "e.capture_rank=1",
    ):
        assert item in SQL

def test_kickoff_reschedules_and_settled_identity_not_overwritten():
    assert "'captured_kickoff_changed'" in SQL
    assert "'matched_settled_schedule'" in SQL
    assert "'matchup_identity_changed'" in SQL
    assert "'capture_not_pregame_relative_to_settled_kickoff'" in SQL
    assert "'not_yet_settled_in_ledger'" in SQL
    assert "source_captured_at < t.event_start_time" in SQL
    assert "t.event_start_time as settled_kickoff" in SQL
    assert "e.captured_kickoff" in SQL

def test_no_pregame_player_prop_claim_or_market_probability_authority():
    assert "true::boolean as captured_schedule_source_provenance_verified" in SQL
    for f in ("player_prop_feature_pit_verified","model_training_eligible","probability_publishable","can_execute"):
        assert f"false::boolean as {f}" in SQL
    assert "security_invoker=true, security_barrier=true" in SQL
    assert "from public, anon, authenticated, service_role" in SQL
    assert "to service_role" in SQL
