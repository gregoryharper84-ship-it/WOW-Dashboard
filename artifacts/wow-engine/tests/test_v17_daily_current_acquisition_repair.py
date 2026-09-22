from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_daily_repair_forbids_historical_manifest_substitution():
    source = (ROOT / "v17" / "sep21_orchestration_integrity_repair.py").read_text()
    assert 'context["force_current_acquisition"]' in source
    assert 'daily._receipt_snapshot_ids(acquisition)' in source
    assert '"historical_manifest_substitution"] = False' in source
    assert '"acquisition_accounting_scope"] = "CURRENT_RUN_ONLY"' in source


def test_daily_repair_does_not_change_model_or_execution_policy():
    source = (ROOT / "v17" / "sep21_orchestration_integrity_repair.py").read_text()
    assert "PRECALIBRATION_SHRINKAGE" not in source
    assert "0.65" not in source
    assert 'response["can_execute"] = False' in source
