# Pytest discovery wrapper for the focused NCAAF trust-layer regressions.
from pathlib import Path

from ncaaf_trust_tests import *  # noqa: F401,F403


def test_ncaaf_sql_governance_contract():
    sql = Path("ncaaf_trust_schema.sql").read_text()
    lowered = sql.lower()
    assert "create table if not exists wow_ncaaf_predictions" in lowered
    assert "create table if not exists wow_ncaaf_outcomes" in lowered
    assert "create or replace view wow_ncaaf_calibration_ledger" in lowered
    assert "check (can_execute = false)" in sql
    assert "ncaaf_qb_status" not in lowered  # evidence column is starting_qb_status, not a shadow status field
    assert "ncaaf_test_only" in lowered
    assert "ncaaf_watch" in lowered
    assert "beat_close" in lowered
    assert "brier_score" in lowered
    assert "log_loss" in lowered
    assert "alter table wow_ncaaf_predictions enable row level security" in lowered
    assert "alter table wow_ncaaf_outcomes enable row level security" in lowered


def test_ncaaf_trust_review_metrics_contract():
    sql = Path("ncaaf_trust_metrics.sql").read_text()
    lowered = sql.lower()
    assert "create or replace view wow_ncaaf_trust_review_metrics" in lowered
    assert "settled_candidates" in lowered
    assert "ncaaf_moneyline_bucket_candidates" in lowered
    assert "clv_positive_rate" in lowered
    assert "hypothetical_unit_risk_roi" in lowered
    assert "selection_price_american" in lowered
    assert "when not o.won then -1.0" in lowered
    assert "false as can_execute" in lowered
    assert "not executed-account roi" in lowered
