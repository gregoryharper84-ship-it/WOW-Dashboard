from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github/workflows/wow-v17-daily-snapshot.yml"


def _acceptance_section() -> str:
    text = WORKFLOW.read_text(encoding="utf-8")
    marker = "nfl-full-slate-exact-once-acceptance:"
    assert marker in text
    return text[text.index(marker):]


def test_nfl_production_acceptance_uses_full_moneyline_and_exceeds_batch_ceiling():
    section = _acceptance_section()
    assert "WOW_NFL_ACCEPTANCE_SLATE_DATE" not in section
    assert 'WOW_NFL_ACCEPTANCE_LOOKAHEAD_DAYS: "42"' in section
    assert "select_future_nfl_acceptance_slate" in section
    assert "ESPN_SCOREBOARD_IDENTITY_ONLY" in section
    assert "candidate.weekday() != 6" in section
    assert '"lanes": ["MONEYLINE"]' in section
    assert '"max_team_events": 32' in section
    assert '"response_mode": "FULL"' in section
    assert "NFL_FULL_SLATE_DISCOVERY_COVERAGE_MISMATCH" in section
    assert "NFL_FULL_SLATE_ACCEPTANCE_DEFERRED_NO_GT12_FUTURE_SLATE" in section
    assert '"status": "DEFERRED_WITH_JUSTIFICATION"' in section
    assert "MODEL_INVOCATION_BUDGET_REACHED" in section
    assert "len(identities) == len(set(identities))" in section


def test_nfl_production_acceptance_is_serialized_after_daily_snapshot():
    section = _acceptance_section()
    assert "needs: daily-snapshot" in section
    assert "Run serialized future NFL full-slate exact-once acceptance" in section


def test_nfl_production_acceptance_preserves_terminal_and_execution_governance():
    section = _acceptance_section()
    assert 'WOW_CAN_EXECUTE: "false"' in section
    assert 'WOW_DRY_RUN_ONLY: "true"' in section
    assert '"V17_TERMINAL_REDUCER"' in section
    assert 'row.get("can_execute") is False' in section
    assert 'result.get("can_execute") is False' in section
    assert '"probability_values_logged": False' in section


def test_acceptance_requires_discovery_reconciliation_and_unique_event_identity():
    section = _acceptance_section()
    assert 'cross_reconciliation.get("row_reconciliation") == "PASS"' in section
    assert 'cross_reconciliation.get("events_accounted") == cross_reconciliation.get("events_discovered")' in section
    assert "NFL_FULL_SLATE_EVENT_ID_MISSING" in section
    assert '"model_invocation_budget_terminal_rows": 0' in section


def test_nfl_production_acceptance_selector_is_identity_only_and_non_aging():
    section = _acceptance_section()
    assert "site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard" in section
    assert '"User-Agent": "WOW-V17-NFL-Acceptance-Identity/1.0"' in section
    assert '"schedule_selector": "ESPN_SCOREBOARD_IDENTITY_ONLY"' in section
    assert '"requested_slate_date": acceptance_slate_date' in section
    assert "WOW_NFL_ACCEPTANCE_SLATE_DATE" not in section
    assert '"can_execute": False' in section
    assert '"probability_values_logged": False' in section
