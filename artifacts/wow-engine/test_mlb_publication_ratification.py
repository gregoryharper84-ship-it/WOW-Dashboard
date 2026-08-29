from pathlib import Path

import yaml


ROOT = Path(__file__).parent
RATIFY_SQL = ROOT / "mlb_publication_ratification.sql"
BRIDGE_SQL = ROOT / "mlb_publishable_score_event_bridge.sql"
OPENAPI = ROOT / "openapi.custom-gpt.template.yaml"


def _sql(path: Path) -> str:
    return " ".join(path.read_text().lower().split())


def test_ratification_migration_creates_no_optimistic_ratified_row():
    sql = _sql(RATIFY_SQL)
    assert "create table if not exists public.wow_mlb_publication_ratification" in sql
    assert "insert into public.wow_mlb_publication_ratification" not in sql
    assert "decision in ('ratified','revoked')" in sql
    assert "can_execute boolean not null default false check (can_execute=false)" in sql


def test_ratification_insert_is_server_timestamped_hashed_and_preconditioned():
    sql = _sql(RATIFY_SQL)
    assert "new.created_at := clock_timestamp()" in sql
    assert "new.evidence_sha256 := encode(" in sql
    assert "extensions.digest(convert_to(new.evidence::text,'utf8'),'sha256')" in sql
    assert "ratification requires the exact latest pass calibration health assessment" in sql
    assert "ratification requires all 11 deployment gates pass" in sql
    assert "ratification requires mlb_event_probability runtime capability available" in sql
    assert "before insert on public.wow_mlb_publication_ratification" in sql


def test_ratification_is_append_only_and_governance_requires_all_latches():
    sql = _sql(RATIFY_SQL)
    assert "before update on public.wow_mlb_publication_ratification" in sql
    assert "before delete on public.wow_mlb_publication_ratification" in sql
    assert "g.gate_count=11 and g.pass_count=11" in sql
    assert "calibration_health_status,'unavailable')='pass'" in sql
    assert "capability_status,'unavailable')='available'" in sql
    assert "decision,'not_ratified')='ratified'" in sql
    assert "lr.calibration_health_assessed_at=h.assessed_at" in sql
    assert "'probability_publishable',publishable" in sql
    assert "'can_execute',false" in sql


def test_publish_bridge_refreshes_material_identity_and_hydrates_new_snapshot_before_identity_check():
    sql = _sql(BRIDGE_SQL)
    capture = "v_capture_refresh:=public.wow_mlb_capture_forward_shadow_schedule( v_active_spec_id,p_requested_slate_date )"
    hydrate = "v_hydrate_refresh:=public.wow_mlb_forward_auto_hydrate_pregame()"
    latest = "order by e0.snapshot_timestamp desc,e0.shadow_event_id desc"
    assert capture in sql
    assert hydrate in sql
    assert sql.index(capture) < sql.index(latest)
    assert "source_snapshot_stale" in sql
    assert "material_identity_refresh_blocked" in sql


def test_publish_bridge_requires_latest_material_snapshot_and_current_lineup_refresh():
    sql = _sql(BRIDGE_SQL)
    assert "order by e0.snapshot_timestamp desc,e0.shadow_event_id desc" in sql
    assert "source_snapshot_stale" in sql
    assert "v_lineup_refresh:=public.wow_mlb_forward_confirm_lineup(e.shadow_event_id)" in sql
    assert "lineup_strict_pregame_provenance_invalid" in sql
    assert "post_lineup_score_snapshot_required" in sql
    assert "s.model_timestamp<e.lineup_confirmed_at" in sql


def test_publish_bridge_requires_publishable_state_before_and_after_external_refresh():
    sql = _sql(BRIDGE_SQL)
    assert "v_publication_attempt boolean := false" in sql
    assert "v_lineup_refresh_ok boolean := false" in sql
    assert sql.count("v_gate:=public.wow_governed_deployment_state()") >= 2
    assert "v_publishable := v_publication_attempt and v_lineup_refresh_ok" in sql
    assert "v_publication_attempt := coalesce(v_gate->>'governed_probability_capability','unavailable')='available' and coalesce((v_gate->>'probability_publishable')::boolean,false)" in sql
    assert "and coalesce(v_gate->>'governed_probability_capability','unavailable')='available' and coalesce((v_gate->>'probability_publishable')::boolean,false)" in sql


def test_publish_bridge_binds_second_gate_read_to_same_calibration_assessment():
    sql = _sql(BRIDGE_SQL)
    assert "v_gate_health_assessed_at:=nullif(v_gate->>'calibration_health_assessed_at','')::timestamptz" in sql
    assert "h.assessed_at is not distinct from v_gate_health_assessed_at" in sql
    assert "calibration_health_state_changed_during_request" in sql


def test_publish_bridge_separates_score_time_and_current_blockers():
    sql = _sql(BRIDGE_SQL)
    assert "v_score_time_blockers:=coalesce(s.blockers,'{}')" in sql
    assert "current publication blockers are recomputed now" in sql
    assert "'current_publication_blockers'" in sql
    assert "'score_time_blockers'" in sql


def test_publish_bridge_numeric_path_remains_non_executable():
    sql = _sql(BRIDGE_SQL)
    assert "if v_publishable and cardinality(v_current_blockers)=0 then" in sql
    assert "'code','governed_probability_published'" in sql
    assert "'probability_fields_withheld',false" in sql
    assert "'probability_publishable',true" in sql
    assert "'can_execute',false" in sql
    assert "'raw_home_probability',s.raw_home_probability" in sql
    assert "'calibrated_home_probability',s.calibrated_home_probability" in sql
    assert "'calibrated_home_lower_bound',s.home_lower_bound" in sql


def test_held_bridge_still_withholds_all_numeric_probability_fields():
    sql = _sql(BRIDGE_SQL)
    held = sql.split("'status','model_scored_held'", 1)[1]
    assert "'probability_fields_withheld',true" in held
    assert "'probability_publishable',false" in held
    assert "'can_execute',false" in held
    assert "'raw_home_probability',s.raw_home_probability" not in held
    assert "'calibrated_home_probability',s.calibrated_home_probability" not in held


def test_custom_gpt_event_contract_documents_both_safe_200_modes_and_fail_closed_errors():
    spec = yaml.safe_load(OPENAPI.read_text())
    operation = spec["paths"]["/score-event"]["post"]
    responses = operation["responses"]
    assert {"200", "401", "409", "422", "500", "503"}.issubset(responses)
    description = operation["description"]
    assert "REAL_FITTED_MODEL_PATH_PROVEN" in description
    assert "GOVERNED_PROBABILITY_PUBLISHED" in description
    assert "can_execute=false" in description


def test_custom_gpt_governance_contract_exposes_independent_publication_latches():
    spec = yaml.safe_load(OPENAPI.read_text())
    properties = spec["paths"]["/governance"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["properties"]
    for key in (
        "deployment_contract_status",
        "calibration_health_status",
        "runtime_capability_status",
        "ratification_status",
        "production_feature_ready",
        "probability_publishable",
        "can_execute",
    ):
        assert key in properties
