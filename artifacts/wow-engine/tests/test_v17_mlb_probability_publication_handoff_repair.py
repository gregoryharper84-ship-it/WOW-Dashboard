from pathlib import Path


SQL_PATH = (
    Path(__file__).resolve().parents[1]
    / "v17"
    / "sql"
    / "20260916_v17_mlb_probability_publication_handoff_repair.sql"
)
SQL = SQL_PATH.read_text()


def test_probability_only_bridge_hydrates_governance_evidence_before_publication_gates():
    hydrate = SQL.index("wow_v17_hydrate_mlb_event_governance_evidence")
    identity = SQL.index("wow_evaluate_event_identity_lock")
    source = SQL.index("wow_refresh_event_source_completeness")
    audit = SQL.index("wow_v17_audit_probability_only_event")
    refresh = SQL.index("wow_v17_probability_only_final_refresh")

    assert hydrate < identity
    assert hydrate < source
    assert hydrate < audit
    assert hydrate < refresh


def test_patch_reuses_certified_hydration_and_does_not_create_proxy_probability():
    assert "p_score_snapshot_id,''{}''::jsonb,p_decision_intent" in SQL
    assert "sportsbook" not in SQL.lower()
    assert "implied_probability" not in SQL.lower()
    assert "calibrated_home_probability=" not in SQL
    assert "calibrated_away_probability=" not in SQL


def test_patch_keeps_fail_closed_publication_chain_and_execution_disabled():
    assert "V17_MLB_PUBLICATION_HANDOFF_HYDRATION_MISSING" in SQL
    assert "V17_MLB_PUBLICATION_GATE_CHAIN_INCOMPLETE" in SQL
    assert "V17_MLB_PUBLICATION_HANDOFF_ORDER_INVALID" in SQL
    assert "can_execute remains false" in SQL
    assert "can_execute=true" not in SQL
    assert "can_execute = true" not in SQL
