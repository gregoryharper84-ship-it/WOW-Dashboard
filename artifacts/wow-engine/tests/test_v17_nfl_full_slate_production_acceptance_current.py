from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github/workflows/wow-v17-daily-snapshot.yml"


def _acceptance_section() -> str:
    text = WORKFLOW.read_text(encoding="utf-8")
    marker = "nfl-full-slate-exact-once-acceptance:"
    assert marker in text
    return text[text.index(marker):]


def test_nfl_production_acceptance_uses_dynamic_future_slate_and_full_moneyline():
    section = _acceptance_section()
    assert 'WOW_NFL_ACCEPTANCE_SLATE_DATE: "2026-10-04"' not in section
    assert 'days_until_next_sunday = (6 - today.weekday()) % 7 or 7' in section
    assert '"requested_slate_date": slate_date' in section
    assert 'result.get("requested_slate_date") == slate_date' in section
    assert '"lanes": ["MONEYLINE"]' in section
    assert '"max_team_events": 32' in section
    assert '"response_mode": "FULL"' in section
    assert "NFL_FULL_SLATE_ACCEPTANCE_REQUIRES_GT12_ROWS" not in section
    assert "NFL_NEXT_SLATE_DISCOVERY_EMPTY" in section
    assert "NFL_NEXT_SLATE_WRONG_DATE_HOLD" in section
    assert "NFL_NEXT_SLATE_EVENT_TIME_INVALID" in section
    assert "NFL_DISCOVERY_EXCEEDS_REQUESTED_LIMIT" in section
    assert "MODEL_INVOCATION_BUDGET_REACHED" in section
    assert "len(identities) == len(set(identities))" in section


def test_nfl_production_acceptance_is_serialized_after_daily_snapshot():
    section = _acceptance_section()
    assert "needs: daily-snapshot" in section
    assert "Run serialized next-Sunday NFL full-slate identity acceptance" in section


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
    assert '"model_terminal_certification": "NOT_PROVEN_BY_DISCOVERY_ACCEPTANCE"' in section


def test_next_sunday_is_strictly_future_for_all_weekdays():
    """Never replay a historical/started Sunday under a live acceptance name."""
    from datetime import date, timedelta

    for day in range(19, 26):  # Mon 2026-10-19 through Sun 2026-10-25
        today = date(2026, 10, day)
        offset = (6 - today.weekday()) % 7 or 7
        selected = today + timedelta(days=offset)
        assert selected > today
        assert selected.weekday() == 6


def test_acceptance_does_not_claim_a_fitted_terminal_result():
    section = _acceptance_section()
    assert "NFL_NEXT_SLATE_EXACT_ONCE_DISCOVERY_PASS" in section
    assert "NOT_PROVEN_BY_DISCOVERY_ACCEPTANCE" in section
    assert "FINAL_APPROVED" not in section
