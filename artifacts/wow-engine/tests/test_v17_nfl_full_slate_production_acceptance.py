from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github/workflows/wow-v17-daily-snapshot.yml"


def test_nfl_production_acceptance_uses_full_moneyline_and_exceeds_batch_ceiling():
    text = WORKFLOW.read_text(encoding="utf-8")
    marker = "nfl-full-slate-exact-once-acceptance:"
    assert marker in text
    section = text[text.index(marker):]
    assert 'WOW_NFL_ACCEPTANCE_SLATE_DATE: "2026-10-04"' in section
    assert '"lanes": ["MONEYLINE"]' in section
    assert '"max_team_events": 32' in section
    assert '"response_mode": "FULL"' in section
    assert "NFL_FULL_SLATE_ACCEPTANCE_REQUIRES_GT12_ROWS" in section
    assert "MODEL_INVOCATION_BUDGET_REACHED" in section
    assert "len(identities) == len(set(identities))" in section


def test_nfl_production_acceptance_preserves_terminal_and_execution_governance():
    text = WORKFLOW.read_text(encoding="utf-8")
    section = text[text.index("nfl-full-slate-exact-once-acceptance:"):]
    assert 'WOW_CAN_EXECUTE: "false"' in section
    assert 'WOW_DRY_RUN_ONLY: "true"' in section
    assert '"V17_TERMINAL_REDUCER"' in section
    assert 'row.get("can_execute") is False' in section
    assert 'result.get("can_execute") is False' in section


def test_acceptance_requires_discovery_reconciliation_and_unique_event_identity():
    text = WORKFLOW.read_text(encoding="utf-8")
    section = text[text.index("nfl-full-slate-exact-once-acceptance:"):]
    assert 'cross_reconciliation.get("row_reconciliation") == "PASS"' in section
    assert 'cross_reconciliation.get("events_accounted") == cross_reconciliation.get("events_discovered")' in section
    assert "NFL_FULL_SLATE_EVENT_ID_MISSING" in section
    assert '"model_invocation_budget_terminal_rows": 0' in section
