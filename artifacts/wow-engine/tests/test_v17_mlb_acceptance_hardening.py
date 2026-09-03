from pathlib import Path

SQL = (
    Path(__file__).resolve().parents[1]
    / "v17"
    / "sql"
    / "20260903_v17_mlb_acceptance_hardening.sql"
).read_text()


def test_warmup_remains_pregame_for_shared_environment_provider():
    assert "codedGameState','') <> 'P'" in SQL
    assert "('Scheduled','Pre-Game','Warmup')" in SQL


def test_healthy_reassessment_does_not_require_exact_ratification_timestamp_match():
    assert "lr.calibration_health_assessed_at=h.assessed_at" not in SQL
    assert "coalesce(h.calibration_health_status,'UNAVAILABLE')='PASS'" in SQL
    assert "coalesce(lr.decision,'NOT_RATIFIED')='RATIFIED'" in SQL


def test_probability_only_promotion_uses_mlb_certified_calibrator_not_market():
    assert "v_probability_only" in SQL
    assert "wow_mlb_v2d_intercept_calibration" in SQL
    assert "wow_mlb_event_fitted_model_artifacts" in SQL
    assert "if not v_probability_only then" in SQL
    assert "insufficient fresh books" in SQL


def test_source_manifest_and_frozen_scoring_snapshot_are_preserved():
    assert "wow_v17_refresh_event_source_manifest" in SQL
    assert "WOW_V17_EVENT_SOURCE_MANIFEST_V1" in SQL
    assert "wow_v17_reconcile_probability_only_snapshot" in SQL
    assert "DO NOTHING" in SQL or "do nothing" in SQL
    assert "retrieved_at',fetched_at" in SQL  # match text removed dynamically from payload hash


def test_execution_remains_disabled():
    assert "'can_execute',false" in SQL
    assert "can_execute=true" not in SQL
