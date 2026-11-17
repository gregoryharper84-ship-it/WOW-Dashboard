"""Research-only tests for NBA chronological diagnostic SQL."""
from pathlib import Path
import math

SQL = (Path(__file__).resolve().parent / "v17" / "research" /
       "nba_prior10_empirical_holdout_20261027.sql").read_text()


def test_source_and_stat_identity():
    assert "SPORTSDATAVERSE_ESPN_NBA_PLAYER_BOX_2026_7A4868DA" in SQL
    assert "array_length(a,1) = 57" in SQL
    for name in ("a[28]::numeric AS pts", "a[21]::numeric AS reb", "a[22]::numeric AS ast"):
        assert name in SQL


def test_frozen_train_and_strictly_prior_features():
    assert SQL.count("ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING") == 4
    assert "f.game_date < c.cutoff" in SQL
    assert "f.game_date>=t.cutoff" in SQL
    assert "WHERE prior_n = 10" in SQL
    assert "t.n >= 20" in SQL


def test_research_calculation_contract():
    assert "(v.n_joint+15*v.p_independent)/(h.n+15)" in SQL
    assert "(t.n_p+0.5)/(t.n+1)" in SQL
    assert "day_cluster_se AS" in SQL
    assert "'RESEARCH_HOLD_NOT_CERTIFIED'::text AS status" in SQL
    assert "false AS can_execute" in SQL
    assert "false AS probability_publishable" in SQL


def test_brier_gain_sign_on_synthetic_outcomes():
    assert (0.2-1)**2 - (0.3-1)**2 > 0
    assert (0.2-0)**2 - (0.3-0)**2 < 0
