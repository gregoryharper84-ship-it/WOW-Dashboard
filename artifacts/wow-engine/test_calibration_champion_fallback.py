from pathlib import Path


MIGRATION = (
    Path(__file__).parent
    / "migrations"
    / "20260830_calibration_champion_fallback.sql"
)


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_01_incomplete_challenger_valid_champion_degrades_without_blocking_serving():
    sql = _sql()
    assert "DEGRADED_CHALLENGER_BLOCKED" in sql
    assert "challenger_blockers" in sql
    assert "FORWARD_SHADOW_NOT_COMPLETED" in sql
    assert "'probability_publishable',true" in sql
    assert "'serving_calibration_health','PASS'" in sql
    assert "'can_execute',false" in sql


def test_02_incomplete_challenger_without_champion_fails_closed():
    sql = _sql()
    assert "NO_VALID_CERTIFIED_CHAMPION" in sql
    assert "'service_status','UNAVAILABLE'" in sql
    assert "'probability_publishable',false" in sql


def test_03_expired_revoked_or_retired_champion_is_ineligible():
    sql = _sql()
    assert "x.revoked_at is null" in sql
    assert "x.retired_at is null" in sql
    assert "x.certification_expires_at is null or x.certification_expires_at > clock_timestamp()" in sql
    assert "x.lifecycle_state = 'CHAMPION'" in sql
    assert "x.active = true" in sql
    assert "x.promoted = true" in sql


def test_04_completed_challenger_is_review_eligible_not_auto_promoted():
    sql = _sql()
    assert "PROMOTION_REVIEW_ELIGIBLE" in sql
    assert "h.forward_shadow_status = 'SUFFICIENT_FOR_REVIEW'" in sql
    # Resolver is read-only/stable: it must not perform the serving-pointer switch.
    resolver = sql.split("create or replace function public.wow_mlb_resolve_serving_probability_model", 1)[1]
    resolver = resolver.split("create or replace function public.wow_mlb_stage_0_5_calibration_precheck", 1)[0]
    assert "update public.wow_mlb_event_fitted_model_artifacts" not in resolver.lower()
    assert "insert into public.wow_mlb_event_fitted_model_artifacts" not in resolver.lower()


def test_05_live_request_cannot_reuse_pregame_champion():
    sql = _sql()
    assert "x.serving_mode = p_requested_mode" in sql
    assert "NO_VALID_CERTIFIED_LIVE_CHAMPION" in sql
    assert "serving_mode <> 'LIVE' or state_schema_version is not null" in sql


def test_06_publication_packet_identifies_exact_serving_artifacts():
    sql = _sql()
    for field in (
        "serving_model_family",
        "serving_model_version",
        "serving_calibration_version",
        "serving_certification_id",
        "governance_hash",
        "feature_schema_version",
        "state_schema_version",
        "market_type",
        "settlement_basis",
        "challenger_status",
        "challenger_blockers",
        "probability_publishable",
        "can_execute",
    ):
        assert field in sql


def test_07_material_update_after_scoring_requires_rerun():
    sql = _sql()
    assert "p_model_timestamp < p_latest_material_update_at" in sql
    assert "STALE_MODEL_RESULT_RERUN_REQUIRED" in sql
    assert "'probability_publishable',false" in sql


def test_08_stage_0_5_resolves_serving_version_and_preserves_challenger_blockers():
    sql = _sql()
    assert "wow_mlb_stage_0_5_calibration_precheck" in sql
    assert "wow_mlb_resolve_serving_probability_model" in sql
    assert "'calibration_precheck_status'" in sql
    assert "r->>'serving_calibration_health' = 'PASS'" in sql
    # No function in this migration rewrites challenger health or clears blockers.
    assert "set blockers = '{}'" not in sql.lower()
    assert "set calibration_health_status = 'pass'" not in sql.lower()
