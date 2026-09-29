from pathlib import Path


def test_priority_runtime_splits_network_settlement_from_db_first_audit():
    repo_root = Path(__file__).resolve().parents[3]
    runtime = (repo_root / "artifacts" / "wow-engine" / "v17" / "prop_priority_lifecycle_runtime.py").read_text()
    route = (repo_root / "artifacts" / "wow-engine" / "v17" / "prop_forward_cohort_route.py").read_text()

    assert '/v17/prop-priority-settlement-run' in runtime
    assert '/v17/prop-priority-durable-audit-run' in runtime
    assert 'run_exact_route_settlement' in runtime
    assert 'run_prop_certification_audit' in runtime
    assert 'run_prop_production_registration_audit' in runtime
    assert 'run_prop_calibrator_candidate_audit' in runtime
    assert 'persist_lifecycle_health' in runtime
    assert 'run_universal_prop_forward_evidence' not in runtime
    assert 'FORWARD_EVIDENCE_KIND = "IMMUTABLE_PREGAME_SETTLED"' in runtime
    assert 'LEGACY_FORWARD_ROUTE = ("MLB", "PITCHER_STRIKEOUTS")' in runtime
    assert 'automatic_certification' in runtime
    assert 'automatic_promotion' in runtime
    assert '"can_execute": False' in runtime
    assert 'scout_route_auth_dependency' in runtime
    assert 'install_priority_prop_lifecycle_runtime' in route


def test_durable_audit_counts_independent_theses_and_exact_artifact_identity():
    repo_root = Path(__file__).resolve().parents[3]
    runtime = (repo_root / "artifacts" / "wow-engine" / "v17" / "prop_priority_lifecycle_runtime.py").read_text()

    assert 'COUNTING_BASIS = "UNIQUE_EVENT_PLAYER_STAT_LINE_THESIS"' in runtime
    assert 'feature_schema_version' in runtime
    assert 'model_artifact_version' in runtime
    assert 'model_artifact_checksum' in runtime
    assert 'calibration_version' in runtime
    assert 'model_ts >= start or locked >= start' in runtime
    assert 'route_theses = {_thesis_key(row) for row in eligible}' in runtime
    assert 'artifact_cohorts' in runtime
    assert 'forward_unsettled_n' in runtime
