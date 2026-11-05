"""Real PostgreSQL security and admission tests for the undeployed SQL candidate.

Requires psycopg + pgserver and a disposable server with pgcrypto. A local
SCORED fixture and mock V17 reducer prove SQL plumbing ONLY; they are NOT a
real fitted model, independently accepted reducer or live sporting probability.
Skipped tests do not constitute production acceptance.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

psycopg = pytest.importorskip("psycopg")
pgserver = pytest.importorskip("pgserver")

SQL = (
    Path(__file__).resolve().parents[1] / "v17" / "sql_candidates"
    / "20261105_multisport_nfl_receipt_admission_UNDEPLOYED.sql"
)
SCORE = str(uuid4())
MODEL = str(uuid4())
CAL = str(uuid4())
SNAP = str(uuid4())
START = datetime.now(timezone.utc) + timedelta(days=2)
SNAP_AT = datetime.now(timezone.utc) - timedelta(hours=2)
MODEL_AT = datetime.now(timezone.utc) - timedelta(hours=1)
HASH = "a" * 64


@pytest.fixture(scope="module")
def pg(tmp_path_factory):
    server = pgserver.get_server(str(tmp_path_factory.mktemp("multisport-admission")))
    try:
        uri = server.get_uri()
        with psycopg.connect(uri, autocommit=True) as db:
            db.execute("create schema if not exists extensions")
            db.execute("create extension if not exists pgcrypto with schema extensions")
            db.execute("create role service_role nologin")
            db.execute("create role anon nologin")
            db.execute("create role authenticated nologin")
            db.execute("grant usage on schema public to service_role, anon, authenticated")
            # Minimal typed specialist tables matching the actual production
            # column names; this fixture is NOT an independently fitted scorer.
            db.execute("""
                create table public.wow_nfl_event_predictions (
                  score_snapshot_id uuid primary key, official_event_id text,
                  event_start_time_utc timestamptz,home_team text,away_team text,
                  selected_participant text,source_snapshot_id uuid,
                  source_snapshot_timestamp timestamptz,model_timestamp timestamptz,
                  model_artifact_id uuid,model_version text,model_output_snapshot jsonb,
                  latest_material_update_timestamp timestamptz,
                  calibration_version text,model_probability_publishable boolean,
                  model_package_valid boolean,provenance_complete boolean,
                  calibration_health_status text,blend_publishable boolean,
                  can_execute boolean,calibrated_home_probability numeric,
                  calibrated_home_lower_bound numeric,calibrated_home_upper_bound numeric,
                  calibrated_away_probability numeric,calibrated_away_lower_bound numeric,
                  calibrated_away_upper_bound numeric
                )
            """)
            db.execute("""
                create table public.wow_nfl_event_fitted_model_artifacts (
                  artifact_id uuid primary key,model_artifact_version text,
                  artifact_checksum text,calibrator_id uuid,lifecycle_state text,
                  active boolean,promoted boolean,can_execute boolean,
                  probability_publishable boolean
                )
            """)
            db.execute("""
                create table public.wow_calibrators (
                  calibrator_id uuid primary key,calibration_version text,
                  active boolean,promoted boolean,validation_status text,
                  health_status text,sport text,market_family text
                )
            """)
            # Deliberately external official reducer interface; the real V17
            # implementation must be used for separate integration acceptance.
            db.execute("""
                create function public.wow_v17_reduce_nfl_event_terminal_label(p_score_snapshot_id uuid)
                returns jsonb language plpgsql set search_path='' as $fn$
                declare available boolean;
                begin
                  select s.provenance_complete and s.model_package_valid and
                    s.model_probability_publishable and s.event_start_time_utc > clock_timestamp()
                    and (s.latest_material_update_timestamp is null or
                         s.model_timestamp >= s.latest_material_update_timestamp)
                    into available from public.wow_nfl_event_predictions s
                    where score_snapshot_id=p_score_snapshot_id;
                  if available is true then
                    return jsonb_build_object('status','PASS',
                      'terminal_label','FINAL_APPROVED','global_terminal_reducer','V17_TERMINAL_REDUCER',
                      'probability_publishable',true,'rank_eligible',true,'can_execute',false,
                      'blockers',jsonb_build_array());
                  end if;
                  return jsonb_build_object('status','HOLD',
                    'terminal_label','MODEL_QUALIFIED_HOLD','global_terminal_reducer','V17_TERMINAL_REDUCER',
                    'probability_publishable',false,'rank_eligible',false,'can_execute',false,
                    'blockers',jsonb_build_array('SPECIALIST_HOLD'));
                end $fn$
            """)
            db.execute("""
                insert into public.wow_nfl_event_fitted_model_artifacts
                (artifact_id,model_artifact_version,artifact_checksum,calibrator_id,
                 lifecycle_state,active,promoted,can_execute,probability_publishable)
                values (%s,'NFL_VERSION',%s,%s,'CHAMPION',true,true,false,true)
            """,(MODEL,HASH,CAL))
            db.execute("""
                insert into public.wow_calibrators
                (calibrator_id,calibration_version,active,promoted,
                 validation_status,health_status,sport,market_family)
                values (%s,'CAL_VERSION',true,true,'PASS','PASS','NFL','OUTRIGHT_WINNER')
            """,(CAL,))
            db.execute("""
                insert into public.wow_nfl_event_predictions
                (score_snapshot_id,official_event_id,event_start_time_utc,home_team,away_team,
                 selected_participant,source_snapshot_id,source_snapshot_timestamp,model_timestamp,
                 model_artifact_id,model_version,model_output_snapshot,calibration_version,
                 model_probability_publishable,model_package_valid,provenance_complete,
                 calibration_health_status,blend_publishable,can_execute,
                 calibrated_home_probability,calibrated_home_lower_bound,calibrated_home_upper_bound,
                 calibrated_away_probability,calibrated_away_lower_bound,calibrated_away_upper_bound)
                values (%s,'official-fixture',%s,'H','A','H',%s,%s,%s,%s,'NFL_VERSION',
                        jsonb_build_object('artifact_checksum',%s),'CAL_VERSION',
                        true,true,true,'PASS',false,false,.61,.55,.65,.39,.35,.45)
            """,(SCORE,START,SNAP,SNAP_AT,MODEL_AT,MODEL,HASH))
            db.execute(SQL.read_text())
        yield uri
    finally:
        cleanup=getattr(server,"cleanup",None)
        if callable(cleanup): cleanup()


def call(uri, function, uuid):
    with psycopg.connect(uri,autocommit=True) as db:
        return db.execute(f"select public.{function}(%s::uuid)",(uuid,)).fetchone()[0]


def test_real_db_admits_only_registered_score_after_real_reducer_interface(pg):
    issued=call(pg,"wow_v17_admit_nfl_event_receipt",SCORE)
    assert issued["status"]=="PERSISTED"
    assert issued["can_execute"] is False
    assert issued["score_snapshot_id"]==SCORE
    assert issued["event_prediction_id"] != SCORE
    check=call(pg,"wow_v17_verify_multisport_event_receipt",issued["event_prediction_id"])
    assert check["status"]=="PASS"
    assert check["terminal_authority"]=="V17_TERMINAL_REDUCER"
    assert check["reducer_receipt_id"]==issued["reducer_receipt_id"]
    with psycopg.connect(pg,autocommit=True) as db:
        assert db.execute("select count(*) from public.wow_v17_multisport_event_prediction_receipts").fetchone()[0] == 1


def test_identical_duplicate_is_idempotent_and_keeps_server_uuid(pg):
    first=call(pg,"wow_v17_admit_nfl_event_receipt",SCORE)
    second=call(pg,"wow_v17_admit_nfl_event_receipt",SCORE)
    assert second["status"]=="ALREADY_PERSISTED"
    assert first["event_prediction_id"]==second["event_prediction_id"]
    assert first["reducer_receipt_id"]==second["reducer_receipt_id"]


@pytest.mark.parametrize("role", ["anon","authenticated"])
def test_untrusted_api_roles_cannot_execute_admission_or_terminal_readback(pg,role):
    with psycopg.connect(pg,autocommit=True) as db:
        db.execute(f"set role {role}")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            db.execute("select public.wow_v17_admit_nfl_event_receipt(%s::uuid)",(SCORE,))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            db.execute("select public.wow_v17_verify_multisport_event_receipt(%s::uuid)",(SCORE,))


def test_service_role_has_no_direct_insert_update_delete_on_ledger(pg):
    with psycopg.connect(pg,autocommit=True) as db:
        db.execute("set role service_role")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            db.execute("delete from public.wow_v17_multisport_event_prediction_receipts")


def test_existing_immutable_receipt_cannot_be_changed_even_by_owner(pg):
    with psycopg.connect(pg,autocommit=True) as db:
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            db.execute("update public.wow_v17_multisport_event_prediction_receipts set home_team='OTHER'")
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            db.execute("delete from public.wow_v17_multisport_event_prediction_receipts")


def test_postscore_model_invalidates_reducer_and_replay(pg):
    # Material update without a new scorer is a reducer HOLD even when all
    # static source/model/certification flags remain true. This catches replay
    # bypasses that return an older immutable receipt without rerunning V17.
    with psycopg.connect(pg,autocommit=True) as db:
        receipt_id=db.execute(
            "select event_prediction_id from public.wow_v17_multisport_event_prediction_receipts"
        ).fetchone()[0]
        db.execute(
            "update public.wow_nfl_event_predictions "
            "set latest_material_update_timestamp=%s where score_snapshot_id=%s",
            (datetime.now(timezone.utc),SCORE),
        )
    second=call(pg,"wow_v17_admit_nfl_event_receipt",SCORE)
    assert second["status"]=="HOLD"
    assert "event_prediction_id" not in second
    first=call(pg,"wow_v17_verify_multisport_event_receipt",str(receipt_id))
    assert first["status"]=="HOLD"
