from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _workflow(name: str) -> str:
    return (ROOT / ".github" / "workflows" / name).read_text()


def test_basketball_maintenance_cannot_go_green_with_blocked_rows():
    text = _workflow("wow-v17-basketball-model-maintenance.yml")
    assert 'blocked_rows = [row for row in payload.get("rows", []) if row.get("status") == "BLOCKED"]' in text
    assert 'if payload.get("status") == "BLOCKED" or blocked_rows:' in text
    assert 'raise RuntimeError("BASKETBALL_MAINTENANCE_BLOCKED:' in text


def test_ncaaf_maintenance_cannot_go_green_when_governed_payload_is_blocked():
    text = _workflow("wow-v17-ncaaf-model-maintenance.yml")
    assert 'if payload.get("status") == "BLOCKED":' in text
    assert 'raise RuntimeError("NCAAF_MAINTENANCE_BLOCKED:' in text


def test_first_six_orchestrator_cannot_hide_blocked_ancillary_lanes():
    text = _workflow("wow-v17-first-six-model-maintenance.yml")
    assert 'blocked_ancillary = {' in text
    assert 'if blocked_ancillary:' in text
    assert 'raise RuntimeError("FIRST_SIX_ANCILLARY_MAINTENANCE_BLOCKED:' in text


def test_terminal_truth_preserves_non_execution_controls():
    for name in (
        "wow-v17-basketball-model-maintenance.yml",
        "wow-v17-ncaaf-model-maintenance.yml",
        "wow-v17-first-six-model-maintenance.yml",
    ):
        text = _workflow(name)
        assert 'WOW_CAN_EXECUTE: "false"' in text
        assert 'WOW_DRY_RUN_ONLY: "true"' in text


def test_ncaaf_compiler_diagnostics_are_visible_without_raw_evidence_payloads():
    text = _workflow("wow-v17-ncaaf-model-maintenance.yml")
    maintenance = text.split("- name: Run governed NCAAF model maintenance", 1)[1]
    maintenance = maintenance.split("  exact-candidate-replay-once:", 1)[0]
    for key in (
        "fresh_acquisition_complete",
        "source_snapshot_n",
        "source_snapshot_persisted_n",
        "feature_status",
        "games_seen",
        "evidence_rows_seen",
        "blocked_games",
        "features_persisted",
        "features_existing",
        "complete_feature_rows",
        "feature_blocker_counts",
        "feature_blocker_samples",
    ):
        assert f'"{key}":' in maintenance
    assert 'for sample in raw_samples[:3]:' in maintenance
    assert 'str(value)[:80]' in maintenance
    assert '"payload":' not in maintenance
    assert 'assert payload.get("can_execute") is False' in maintenance
    assert 'if payload.get("status") == "BLOCKED":' in maintenance

