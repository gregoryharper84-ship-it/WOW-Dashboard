from pathlib import Path


ROOT = Path(__file__).parent
RATIFY_SQL = ROOT / "mlb_publication_ratification.sql"
BRIDGE_SQL = ROOT / "mlb_publishable_score_event_bridge.sql"


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


def test_publish_bridge_requires_latest_material_snapshot_and_current_lineup_refresh():
    sql = _sql(BRIDGE_SQL)
    assert "order by e0.snapshot_timestamp desc,e0.shadow_event_id desc" in sql
    assert "source_snapshot_stale" in sql
    assert "v_lineup_refresh:=public.wow_mlb_forward_confirm_lineup(e.shadow_event_id)" in sql
    assert "lineup_strict_pregame_provenance_invalid" in sql
    assert "post_lineup_score_snapshot_required" in sql
    assert "s.model_timestamp<e.lineup_confirmed_at" in sql


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
