from __future__ import annotations

from pathlib import Path

from v17 import ncaab_sportsdataverse_candidate as ncaab
from v17 import soccer_openfootball_candidate as soccer
from v17 import tennis_valuebet_candidate as tennis


def test_ncaab_source_contract_is_cc_by_and_does_not_define_market_features():
    assert ncaab.SOURCE_LICENSE == "CC-BY-4.0"
    assert "sportsdataverse" in ncaab.SOURCE_POLICY_ID.lower()
    assert all("odds" not in name and "price" not in name and "market" not in name for name in ncaab.FEATURE_NAMES)


def test_soccer_source_contract_is_cc0_and_true_three_way():
    assert soccer.SOURCE_LICENSE == "CC0-1.0"
    assert set(soccer.COMPETITIONS) == {"EPL", "BUNDESLIGA", "LALIGA", "SERIE_A", "LIGUE_1"}
    assert all("odds" not in name and "price" not in name and "market" not in name for name in soccer.FEATURE_NAMES)


def test_tennis_contract_ignores_odds_and_separates_atp_wta():
    assert tennis.SOURCE_LICENSE == "CC-BY-4.0"
    assert tennis.TOURS == ("ATP", "WTA")
    assert all("odds" not in name and "price" not in name and "market" not in name for name in tennis.FEATURE_NAMES)
    assert tennis._completed_score("6-4 6-3") is True
    assert tennis._completed_score("6-4 2-1 ret") is False
    assert tennis._completed_score("W/O") is False


def test_first_six_workflow_never_runs_maintenance_from_pull_request_and_push_is_marker_gated():
    repo_root = Path(__file__).resolve().parents[2]
    text = (repo_root / ".github" / "workflows" / "wow-v17-first-six-model-maintenance.yml").read_text()
    assert "pull_request:" in text
    assert "github.event_name == 'schedule'" in text
    assert "github.event_name == 'workflow_dispatch'" in text
    assert "github.event_name == 'push'" in text
    assert "github.ref == 'refs/heads/main'" in text
    assert "contains(github.event.head_commit.message, '[RUN_FIRST_SIX]')" in text
    assert 'WOW_CAN_EXECUTE: "false"' in text
    assert 'WOW_DRY_RUN_ONLY: "true"' in text
