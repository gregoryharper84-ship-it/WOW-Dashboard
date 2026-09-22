from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_daily_repair_forces_current_acquisition_without_dropping_existing_sports():
    source = (ROOT / "v17" / "sep21_orchestration_integrity_repair.py").read_text()
    assert 'context["force_current_acquisition"]' in source
    assert 'return original_manifest(*args, **kwargs)' in source
    assert 'daily._receipt_snapshot_ids(acquisition)' in source
    assert '"historical_manifest_substitution"] = False' in source
    assert '"preexisting_canonical_slate_rows"] = preexisting_rows' in source
    assert '"preexisting_canonical_rows_included"] = preexisting_rows > 0' in source
    assert "CURRENT_RUN_PRODUCER_AND_PREEXISTING_CANONICAL_SLATE_SEPARATED" in source


def test_daily_repair_publishes_prop_sport_parity_diagnostics():
    source = (ROOT / "v17" / "sep21_orchestration_integrity_repair.py").read_text()
    assert "from v17.prop_sport_parity import prop_sport_parity_summary" in source
    assert 'response["prop_sport_parity"] = prop_sport_parity_summary()' in source


def test_daily_repair_does_not_change_model_or_execution_policy():
    source = (ROOT / "v17" / "sep21_orchestration_integrity_repair.py").read_text()
    assert "PRECALIBRATION_SHRINKAGE" not in source
    assert "0.65" not in source
    assert 'response["can_execute"] = False' in source
