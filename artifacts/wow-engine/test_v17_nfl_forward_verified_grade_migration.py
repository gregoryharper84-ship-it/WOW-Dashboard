"""P0 historical NFL grade integrity: SQL view is private, non-mutating,
and fail-closes against inconsistent settled outcomes/prediction provenance.

The actual stage proof exercise lives in tests/test_v17_nfl_calibration_producer_receipt.py.
This contract test makes the separate DB migration's safety requirements visible in
native wow-engine verification without a live Supabase connection.
"""
from pathlib import Path


SQL_RAW = (
    Path(__file__).parent
    / "migrations"
    / "20261105_nfl_forward_verified_grades.sql"
).read_text(encoding="utf-8")
SQL = SQL_RAW.lower()


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
        # Match _safe_log_loss() so true extreme pregame probabilities remain eligible.
        "greatest(least(g.calibrated_probability, 1.0 - 0.000000000001), 0.000000000001)",
    ):
        assert guard in SQL, guard


def test_reconciliation_preserves_immutable_grade_ledger_and_private_rls():
    assert "with (security_invoker = true, security_barrier = true)" in SQL
    assert "revoke all on public.wow_nfl_forward_verified_grades from public, anon, authenticated" in SQL
    assert "grant select on public.wow_nfl_forward_verified_grades to service_role" in SQL
    for forbidden in ("delete from", "update public.wow_nfl_forward_shadow_grades", "truncate table", "drop trigger"):
        assert forbidden not in SQL


def test_sql_view_excludes_actual_corrupt_historical_grade_shapes():
    """Execute the actual view SELECT with SQLite-compatible numeric casts.

    SQLite's core boolean/math expressions mirror the relational filters here;
    this is a local adversarial check, not a substitute for Supabase migration
    application and privileged production readback.
    """
    import math
    import sqlite3

    db = sqlite3.connect(":memory:")
    db.execute("attach database ':memory:' as public")
    db.execute("""
        create table public.wow_nfl_forward_shadow_grades (
          schema_version text, event_prediction_id text, official_event_id text,
          source_game_id text, model_version text, selected_participant text,
          prediction_created_at text, event_start_time_utc text,
          calibrated_probability real, calibrated_lower_bound real,
          calibrated_upper_bound real, outcome integer, brier real,
          log_loss real, can_execute integer
        )
    """)
    db.execute("""
        create table public.wow_nfl_event_predictions (
          event_prediction_id text, official_event_id text, model_version text,
          selected_participant text, home_team text, away_team text,
          created_at text, event_start_time_utc text,
          model_package_valid integer, provenance_complete integer,
          model_probability_publishable integer, can_execute integer,
          calibrated_selection_probability real,
          calibrated_selection_lower_bound real,
          calibrated_selection_upper_bound real
        )
    """)
    db.execute("""
        create table public.wow_nfl_training_games (
          game_id text, home_score integer, away_score integer,
          home_win integer, tie integer, can_execute integer
        )
    """)

    def add(event, *, home=27, away=17, won=True, tied=False,
            grade_outcome=1, brier_override=None, probability=0.65):
        pid = f"prediction-{event}"
        created, kickoff = "2026-11-01T12:00:00Z", "2026-11-01T18:00:00Z"
        lower = probability / 2
        upper = min(0.99, probability + 0.10)
        clipped = max(min(probability, 1 - 1e-12), 1e-12)
        log_loss = -math.log(clipped if grade_outcome else 1 - clipped)
        brier = (probability - grade_outcome) ** 2 if brier_override is None else brier_override
        db.execute(
            "insert into public.wow_nfl_event_predictions values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (pid, event, "NFL_FITTED", "H", "H", "A", created, kickoff,
             1, 1, 1, 0, probability, lower, upper),
        )
        db.execute(
            "insert into public.wow_nfl_training_games values(?,?,?,?,?,?)",
            (event, home, away, int(won), int(tied), 0),
        )
        db.execute(
            "insert into public.wow_nfl_forward_shadow_grades values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("NFL_FORWARD_SHADOW_GRADE_V1", pid, event, event, "NFL_FITTED",
             "H", created, kickoff, probability, lower, upper,
             grade_outcome, brier, log_loss, 0),
        )

    add("valid")
    add("historical_tie_with_false_flag", home=27, away=27, won=False)
    add("score_flag_conflict", home=27, away=17, won=False)
    add("invalid_stored_outcome", grade_outcome=0)
    add("invalid_metric", brier_override=0.0)
    add("valid_extreme_probability", probability=0.00000000000001)

    # Only remove the PostgreSQL-specific cast; test the SQL view's actual
    # SELECT predicates rather than a second reimplementation of those rules.
    selected = "select" + SQL_RAW.split("as\nselect", 1)[1].split(";\n\n--", 1)[0]
    selected = selected.replace("::double precision", "")
    rows = db.execute(selected).fetchall()
    assert {row[0] for row in rows} == {"valid", "valid_extreme_probability"}
