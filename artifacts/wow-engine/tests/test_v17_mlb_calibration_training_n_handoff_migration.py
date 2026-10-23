from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "20261007_v17_mlb_calibration_training_n_handoff.sql"


def _sql() -> str:
    return MIGRATION.read_text()


def test_calibration_handoff_requires_exact_immutable_identities():
    sql = _sql()
    assert "r.scoring_snapshot_id is distinct from p_score_snapshot_id" in sql
    assert "where calibration_id=s.calibration_id" in sql
    assert "c.method is distinct from s.calibration_method" in sql
    assert "r.calibration_version is distinct from c.calibration_id::text" in sql
    assert "r.calibration_method is distinct from c.method" in sql


def test_calibration_handoff_writes_only_existing_training_n():
    sql = _sql()
    helper = sql.split(
        "create or replace function public.wow_v17_bind_mlb_score_calibration_metadata", 1
    )[1].split("do $patch$", 1)[0]
    assert "set calibration_training_n=c.prior_games" in helper
    assert "raw_home_probability=" not in helper
    assert "raw_away_probability=" not in helper
    assert "calibrated_home_probability=" not in helper
    assert "calibrated_away_probability=" not in helper
    assert "independent_home_probability=" not in helper
    assert "independent_away_probability=" not in helper
    assert "favorite_failure_paths_json=" not in helper
    assert "'probabilities_recomputed',false" in helper
    assert "'calibration_recomputed',false" in helper
    assert "'can_execute',false" in helper


def test_bridge_binds_metadata_before_audit_and_health():
    sql = _sql()
    replacement = next(
        line for line in sql.splitlines() if line.strip().startswith("replacement text :=")
    )
    assert replacement.index("wow_v17_bind_mlb_score_calibration_metadata") < replacement.index(
        "wow_v17_audit_probability_only_event"
    )
    assert replacement.index("wow_v17_audit_probability_only_event") < replacement.index(
        "wow_v17_assess_mlb_event_calibration_health"
    )


def test_global_terminal_and_rank_authority_are_not_modified():
    sql = _sql()
    helper = sql.split(
        "create or replace function public.wow_v17_bind_mlb_score_calibration_metadata", 1
    )[1].split("do $patch$", 1)[0]
    assert "wow_reduce_event_terminal_label" not in helper
    assert "rank_eligible" not in helper
    assert "probability_publishable" not in helper
