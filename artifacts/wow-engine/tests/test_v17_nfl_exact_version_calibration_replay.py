"""Regression coverage for read-only, exact-version NFL forward calibration replay."""
from pathlib import Path
import re

SQL = Path(__file__).resolve().parents[1] / "scripts" / "v17_nfl_exact_version_calibration_replay.sql"


def test_replay_uses_immutable_verified_grades_and_exact_version_bins():
    sql = SQL.read_text()
    assert "public.wow_nfl_forward_verified_grades" in sql
    assert "GROUP BY model_version,decile" in sql
    assert "WHEN p >= 0.8 AND p < 0.9 THEN 8" in sql
    assert "abs(bins.mean_p-bins.win_rate)" in sql
    assert "GROUP BY model_version" in sql


def test_recomputes_outcomes_and_checks_corrupted_stored_metrics():
    sql = SQL.read_text()
    for s in (
        "power(p-y,2)",
        "ln(greatest(1e-12::double precision",
        "stored_brier",
        "stored_log_loss",
        "metric_row_mismatches",
        "invalid_probabilities",
        "count(DISTINCT official_event_id)",
        "count(DISTINCT event_prediction_id)",
    ):
        assert s in sql


def test_replay_is_non_authorizing_and_contains_no_mutations():
    sql = SQL.read_text()
    text = re.sub(r"--[^\n]*", "", sql)
    for forbidden in ("INSERT", "UPDATE", "DELETE", "MERGE", "ALTER", "DROP",
                      "CREATE", "GRANT", "REVOKE", "TRUNCATE", "CALL", "EXECUTE"):
        assert not re.search(r"\b"+forbidden+r"\b", text, re.I), forbidden
    for required in (
        "FALSE AS certification_approved", "FALSE AS probability_publishable",
        "FALSE AS can_execute", "REQUIRES_INDEPENDENT_REVIEW",
    ):
        assert required in sql


def test_replay_fails_closed_on_blank_model_version():
    sql = SQL.read_text()
    assert "NULLIF(btrim(model_version),'') IS NULL" in sql
    assert "missing_model_versions <> 0" in sql

def test_replay_uses_authoritative_clipped_log_loss():
    sql = SQL.read_text()
    assert "greatest(1e-12::double precision" in sql
    assert "least(p, (1.0 - 1e-12::double precision))" in sql


def test_ece_uses_explicit_bin_boundaries_to_avoid_multiply_rounding():
    sql = SQL.read_text()
    assert "WHEN p >= 0.8 AND p < 0.9 THEN 8" in sql
    assert "WHEN p >= 0.9 AND p <= 1.0 THEN 9" in sql
    assert "least(9,floor(p*10)::int)" not in sql
