"""Regression: evidence auditing may not turn SHADOW models into certified winners."""
from pathlib import Path
import re

SQL = Path(__file__).resolve().parents[1] / 'scripts' / 'v17_certification_promotion_preflight.sql'


def test_real_registry_names_and_canonical_certification_boundary():
    sql = SQL.read_text()
    assert 'public.wow_team_event_specialist_certifications' in sql
    for table in (
        'wow_basketball_team_event_model_artifacts',
        'wow_basketball_team_event_calibrators',
        'wow_ncaaf_fitted_model_artifacts',
        'wow_prop_fitted_model_artifacts',
        'wow_nfl_forward_calibration_health',
    ):
        assert f'public.{table}' in sql
    for guessed in ('v17_model_registry', 'v17_slate_candidates', 'v17_governance_promotion_receipts'):
        assert guessed not in sql


def test_audit_has_no_mutating_statement_or_production_grants():
    sql = SQL.read_text()
    stripped = re.sub(r'--[^\n]*', '', sql)
    assert re.search(r'\bWITH\b', stripped, flags=re.I)
    for keyword in ('INSERT','UPDATE','DELETE','MERGE','ALTER','DROP','CREATE','GRANT',
                    'REVOKE','TRUNCATE','CALL','EXECUTE','PERFORM','SECURITY DEFINER'):
        assert not re.search(r'\b' + keyword.replace(' ',r'\s+') + r'\b', stripped, flags=re.I), keyword


def test_shadow_evidence_cannot_claim_terminal_acceptance():
    sql = SQL.read_text()
    assert "'CERTIFICATION_REPLAY_REQUIRED'" in sql
    assert "'TEAM_EVENT_SPECIALIST_ARTIFACT_NOT_CERTIFIED'" in sql
    assert "'DO_NOT_CERTIFY_YET'" in sql
    assert "'PROP_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND'" in sql
    assert 'FALSE AS can_execute' in sql
    assert 'FALSE AS preflight_authorizes_publication' in sql
    assert "'RECEIPT_PRESENT_REQUIRES_ROUTE_REPLAY'" in sql


def test_no_invented_metric_thresholds_or_blanket_promotion():
    sql=SQL.read_text()
    for token in ('0.2350','0.0500','9065','500','WILSON_CALIBRATED_LOWER_BOUND',
                  "'CERTIFIED_ACTIVE'", "'SHADOW'::text"):
        assert token not in sql
