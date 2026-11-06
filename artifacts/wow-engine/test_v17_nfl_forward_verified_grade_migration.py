"""P0 historical NFL grade integrity: SQL view is private, non-mutating,
and fail-closes against inconsistent settled outcomes/prediction provenance.

The actual stage proof exercise lives in tests/test_v17_nfl_calibration_producer_receipt.py.
This contract test makes the separate DB migration's safety requirements visible in
native wow-engine verification without a live Supabase connection.
"""
from pathlib import Path


SQL = (
    Path(__file__).parent
    / "migrations"
    / "20261105_nfl_forward_verified_grades.sql"
).read_text(encoding="utf-8").lower()


def test_verified_grade_view_reconciles_original_prediction_and_current_outcome():
    assert "create or replace view public.wow_nfl_forward_verified_grades" in SQL
    assert "from public.wow_nfl_forward_shadow_grades as g" in SQL
    assert "join public.wow_nfl_event_predictions as p" in SQL
    assert "join public.wow_nfl_training_games as o" in SQL
    for guard in (
        "p.event_prediction_id = g.event_prediction_id",
        "o.game_id = g.official_event_id",
        "g.official_event_id = p.official_event_id",
        "g.model_version = p.model_version",
        "g.selected_participant = p.selected_participant",
        "g.prediction_created_at = p.created_at",
        "p.created_at < p.event_start_time_utc",
        "o.tie is false",
        "o.home_score <> o.away_score",
        "o.home_win = (o.home_score > o.away_score)",
        "and g.outcome = case",
        "g.brier - power(",
        "g.log_loss - case",
    ):
        assert guard in SQL, guard


def test_reconciliation_preserves_immutable_grade_ledger_and_private_rls():
    assert "with (security_invoker = true, security_barrier = true)" in SQL
    assert "revoke all on public.wow_nfl_forward_verified_grades from public, anon, authenticated" in SQL
    assert "grant select on public.wow_nfl_forward_verified_grades to service_role" in SQL
    for forbidden in ("delete from", "update public.wow_nfl_forward_shadow_grades", "truncate table", "drop trigger"):
        assert forbidden not in SQL
