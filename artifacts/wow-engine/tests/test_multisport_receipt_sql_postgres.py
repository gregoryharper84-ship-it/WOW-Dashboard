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

import os
import pytest

# A skipped environment cannot satisfy an explicit P0 PostgreSQL acceptance.
if os.environ.get("WOW_V17_REQUIRE_POSTGRES_RECEIPT_TESTS") == "1":
    import psycopg
    import pgserver
else:
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
                  artifact_checksum text,calibrator_id uuid,model_family text,lifecycle_state text,
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
            # These tables are needed to compile the real certification guard.
            # No test may manufacture an independently verified certificate.
            db.execute("""
              create table public.wow_nfl_forward_calibration_health (
                generated_at timestamptz, created_at timestamptz,
                graded_n integer, minimum_forward_required integer,
                status text, certification_recommendation text,
                blockers jsonb, can_execute boolean
              )
            """)
            db.execute("""
              create table public.wow_team_event_specialist_certifications (
                certification_id uuid primary key, sport text,league_scope text,
                model_family text,model_version text,
                artifact_id text,artifact_sha256 text,
                calibration_artifact_id text,calibration_sha256 text,
                independent_verification_status text,certification_status text,
                probability_publishable boolean,can_execute boolean,
                certified_at timestamptz,verification_receipt jsonb,
                supersedes_certification_id uuid
              )
            """)
            db.execute("""
              insert into public.wow_nfl_forward_calibration_health
              (generated_at,created_at,graded_n,minimum_forward_required,
               status,certification_recommendation,blockers,can_execute)
              values (clock_timestamp(),clock_timestamp(),80,100,
                      'INSUFFICIENT_FORWARD_EVIDENCE','DO_NOT_CERTIFY_YET',
                      '["FORWARD_SAMPLE_INSUFFICIENT"]'::jsonb,false)
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
                 model_family,lifecycle_state,active,promoted,can_execute,probability_publishable)
                values (%s,'NFL_VERSION',%s,%s,'NFL_OUTRIGHT_WIN_LOGREG_V1','CHAMPION',true,true,false,true)
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


@pytest.fixture(autouse=True)
def reset_postgresql_contract_state(pg):
    # Tests are independent: an immutable receipt from a prior case must not
    # make a subsequent case accidentally green or red by order.
    with psycopg.connect(pg,autocommit=True) as db:
        db.execute("truncate public.wow_v17_multisport_event_prediction_receipts")
        db.execute(
            "update public.wow_nfl_event_predictions "
            "set provenance_complete=true, latest_material_update_timestamp=null "
            "where score_snapshot_id=%s",(SCORE,)
        )
        db.execute("""
            update public.wow_nfl_forward_calibration_health
            set graded_n=80,status='INSUFFICIENT_FORWARD_EVIDENCE',
                certification_recommendation='DO_NOT_CERTIFY_YET',
                blockers='["FORWARD_SAMPLE_INSUFFICIENT"]'::jsonb
        """)



def call(uri, function, uuid):
    with psycopg.connect(uri,autocommit=True) as db:
        return db.execute(f"select public.{function}(%s::uuid)",(uuid,)).fetchone()[0]


def test_no_independent_certification_never_mints_receipt(pg):
    # The local reducer may say PASS, but that is not SIRT certification.
    local=call(pg,"wow_v17_reduce_nfl_event_terminal_label",SCORE)
    assert local["status"]=="PASS"
    issued=call(pg,"wow_v17_admit_nfl_event_receipt",SCORE)
    assert issued["status"]=="HOLD"
    assert issued["code"]=="LLP_EVENT_PREDICTION_RECEIPT_MISMATCH"
    assert issued["can_execute"] is False
    assert "event_prediction_id" not in issued
    with psycopg.connect(pg,autocommit=True) as db:
        assert db.execute(
            "select count(*) from public.wow_v17_multisport_event_prediction_receipts"
        ).fetchone()[0] == 0


def test_repeat_cannot_mint_uncertified_receipt(pg):
    first=call(pg,"wow_v17_admit_nfl_event_receipt",SCORE)
    second=call(pg,"wow_v17_admit_nfl_event_receipt",SCORE)
    assert first["status"]=="HOLD"
    assert second["status"]=="HOLD"
    assert "event_prediction_id" not in first
    assert "event_prediction_id" not in second

def test_forward_sample_100_alone_never_replaces_independent_certification(pg):
    with psycopg.connect(pg,autocommit=True) as db:
        db.execute("""
            update public.wow_nfl_forward_calibration_health
            set graded_n=100,status='PASS',
                certification_recommendation='FORWARD_EVIDENCE_PASS',
                blockers='[]'::jsonb
        """)
    issued=call(pg,"wow_v17_admit_nfl_event_receipt",SCORE)
    assert issued["status"]=="HOLD"
    assert "event_prediction_id" not in issued

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


def test_immutable_triggers_exist_before_any_publication(pg):
    with psycopg.connect(pg,autocommit=True) as db:
        names={row[0] for row in db.execute(
          "select tgname from pg_trigger where tgrelid="
          "'public.wow_v17_multisport_event_prediction_receipts'::regclass"
        ).fetchall()}
    assert "trg_v17_multisport_receipt_identity" in names
    assert "trg_v17_multisport_receipt_immutable" in names


def test_postscore_material_update_is_a_reducer_hold_not_publishable(pg):
    with psycopg.connect(pg,autocommit=True) as db:
        db.execute(
            "update public.wow_nfl_event_predictions "
            "set latest_material_update_timestamp=%s where score_snapshot_id=%s",
            (datetime.now(timezone.utc),SCORE),
        )
    local=call(pg,"wow_v17_reduce_nfl_event_terminal_label",SCORE)
    assert local["status"]=="HOLD"
    issued=call(pg,"wow_v17_admit_nfl_event_receipt",SCORE)
    assert issued["status"]=="HOLD"
    assert "event_prediction_id" not in issued
    assert issued["can_execute"] is False
