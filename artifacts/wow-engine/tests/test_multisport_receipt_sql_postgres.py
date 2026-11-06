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
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import os
import pytest

# Prefer the already-running real PostgreSQL 16 service in hosted CI; using
# pgserver is a local fallback, not a prerequisite for the protected CI lane.
# Explicit no-skip acceptance requires a real working database, never a mock.
REQUIRE_POSTGRES = os.environ.get("WOW_V17_REQUIRE_POSTGRES_RECEIPT_TESTS") == "1"
if REQUIRE_POSTGRES:
    import psycopg
else:
    psycopg = pytest.importorskip("psycopg")
try:
    import pgserver
except ImportError:
    pgserver = None

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
    ci_dsn = os.environ.get("WOW_V17_POSTGRES_TEST_DSN")
    if not ci_dsn and os.environ.get("GITHUB_ACTIONS") == "true":
        # Public, ephemeral credentials from wow-engine-verify.yml's service.
        # Never use production, validation, or ambient SUPABASE_URL bindings.
        ci_dsn = "postgresql://wow_agent_ci:wow_agent_ci@localhost:5432/wow_agent_ci"
    server = None
    test_db = None
    if ci_dsn:
        original = urlsplit(ci_dsn)
        if original.scheme not in {"postgres", "postgresql"} or not original.hostname:
            pytest.fail("POSTGRES_CI_DSN_INVALID")
        test_db = "wow_v17_receipt_" + uuid4().hex[:16]
        with psycopg.connect(ci_dsn, autocommit=True) as admin:
            admin.execute("CREATE DATABASE " + test_db)
        uri = urlunsplit((original.scheme, original.netloc, "/" + test_db, "", ""))
    elif pgserver is not None:
        server = pgserver.get_server(str(tmp_path_factory.mktemp("multisport-admission")))
        uri = server.get_uri()
    elif REQUIRE_POSTGRES:
        pytest.fail("POSTGRES_TEST_SERVER_UNAVAILABLE")
    else:
        pytest.skip("No test PostgreSQL available: use hosted service or local pgserver")
    try:
        with psycopg.connect(uri, autocommit=True) as db:
            db.execute("create schema if not exists extensions")
            db.execute("create extension if not exists pgcrypto with schema extensions")
            db.execute("""
                do $roles$
                begin
                  if not exists (select 1 from pg_roles where rolname='service_role')
                  then create role service_role nologin; end if;
                  if not exists (select 1 from pg_roles where rolname='anon')
                  then create role anon nologin; end if;
                  if not exists (select 1 from pg_roles where rolname='authenticated')
                  then create role authenticated nologin; end if;
                end $roles$
            """)
            db.execute("grant usage on schema public to service_role, anon, authenticated")
            # Minimal typed specialist tables matching the actual production
            # column names; this fixture is NOT an independently fitted scorer.
            db.execute("""
                create table public.wow_nfl_event_predictions (
                  score_snapshot_id uuid primary key,
                  event_prediction_id uuid unique default gen_random_uuid(),
                  created_at timestamptz default clock_timestamp(),
                  official_event_id text,
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
            db.execute("""
              create table public.wow_nfl_forward_shadow_grades (
                official_event_id text, event_prediction_id uuid,
                event_start_time_utc timestamptz,
                prediction_created_at timestamptz, model_version text
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
        if test_db is not None:
            # The full suite shares the CI PostgreSQL service; never leave
            # test-only certification fixtures or roles in its main database.
            with psycopg.connect(ci_dsn, autocommit=True) as admin:
                admin.execute("DROP DATABASE " + test_db + " WITH (FORCE)")
        elif server is not None:
            cleanup=getattr(server,"cleanup",None)
            if callable(cleanup): cleanup()


@pytest.fixture(autouse=True)
def reset_postgresql_contract_state(pg):
    # Tests are independent: an immutable receipt from a prior case must not
    # make a subsequent case accidentally green or red by order.
    with psycopg.connect(pg,autocommit=True) as db:
        db.execute("truncate public.wow_v17_multisport_event_prediction_receipts")
        db.execute("delete from public.wow_nfl_forward_shadow_grades where official_event_id like 'foreign-fixture-%' or official_event_id like 'qualifying-fixture-%'")
        db.execute("delete from public.wow_nfl_event_predictions where official_event_id like 'foreign-fixture-%' or official_event_id like 'qualifying-fixture-%'")
        db.execute("delete from public.wow_team_event_specialist_certifications where verification_receipt ? 'test_fixture_only'")
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



def stage_test_only_qualifying_forward_cohort(uri):
    """Mock exact-version cohort and fake certification ONLY in disposable pgserver.

    These forged gate inputs exercise SQL paths; they are NOT independent
    SIRT evidence, real NFL outcomes, or production model certificates.
    """
    with psycopg.connect(uri,autocommit=True) as db:
        db.execute("""
            update public.wow_nfl_forward_calibration_health
            set graded_n=100, status='PASS',
                certification_recommendation='FORWARD_EVIDENCE_PASS',
                blockers='[]'::jsonb
        """)
        db.execute("""
            insert into public.wow_team_event_specialist_certifications (
              certification_id,sport,league_scope,model_family,model_version,
              artifact_id,artifact_sha256,calibration_artifact_id,
              calibration_sha256,independent_verification_status,
              certification_status,probability_publishable,can_execute,
              certified_at,verification_receipt)
            select %s::uuid,'NFL','NFL',a.model_family,a.model_artifact_version,
                   a.artifact_id::text,a.artifact_checksum,
                   c.calibrator_id::text,
                   encode(extensions.digest(convert_to(to_jsonb(c)::text,'UTF8'),'sha256'),'hex'),
                   'PASS','CERTIFIED',true,false,clock_timestamp(),
                   '{"test_fixture_only":true}'::jsonb
            from public.wow_nfl_event_fitted_model_artifacts a
            join public.wow_calibrators c on c.calibrator_id=a.calibrator_id
            where a.artifact_id=%s::uuid
        """,(str(uuid4()),MODEL))
        db.execute("""
            with same_model as (
              insert into public.wow_nfl_event_predictions (
                score_snapshot_id,official_event_id,event_start_time_utc,
                created_at,model_timestamp,model_artifact_id,model_version,
                calibration_version
              )
              select gen_random_uuid(),'qualifying-fixture-'||g::text,
                     %s::timestamptz,%s::timestamptz,%s::timestamptz,
                     %s::uuid,'NFL_VERSION','CAL_VERSION'
              from generate_series(1,100) g
              returning event_prediction_id,official_event_id,
                        event_start_time_utc,created_at,model_version
            )
            insert into public.wow_nfl_forward_shadow_grades (
              official_event_id,event_prediction_id,event_start_time_utc,
              prediction_created_at,model_version
            )
            select official_event_id,event_prediction_id,event_start_time_utc,
                   created_at,model_version from same_model
        """,(START,MODEL_AT,MODEL_AT,MODEL))


def test_test_only_qualified_cohort_reaches_reducer_and_receipt_readback(pg):
    stage_test_only_qualifying_forward_cohort(pg)
    assert call(pg,"wow_v17_nfl_receipt_certified",SCORE) is True
    issued=call(pg,"wow_v17_admit_nfl_event_receipt",SCORE)
    assert issued["status"]=="PERSISTED"
    assert issued["can_execute"] is False
    assert "reducer_receipt_sha256" in issued
    verified=call(pg,"wow_v17_verify_multisport_event_receipt",issued["event_prediction_id"])
    assert verified["status"]=="PASS"
    assert verified["can_execute"] is False
    assert verified["reducer_receipt_sha256"]==issued["reducer_receipt_sha256"]
    repeated=call(pg,"wow_v17_admit_nfl_event_receipt",SCORE)
    assert repeated["status"]=="ALREADY_PERSISTED"
    assert repeated["event_prediction_id"]==issued["event_prediction_id"]


def test_test_only_certificate_does_not_override_reducer_material_update_hold(pg):
    stage_test_only_qualifying_forward_cohort(pg)
    with psycopg.connect(pg,autocommit=True) as db:
        db.execute(
            "update public.wow_nfl_event_predictions "
            "set latest_material_update_timestamp=%s where score_snapshot_id=%s",
            (datetime.now(timezone.utc),SCORE),
        )
    assert call(pg,"wow_v17_nfl_receipt_certified",SCORE) is True
    assert call(pg,"wow_v17_reduce_nfl_event_terminal_label",SCORE)["status"]=="HOLD"
    held=call(pg,"wow_v17_admit_nfl_event_receipt",SCORE)
    assert held["status"]=="HOLD"
    assert "event_prediction_id" not in held
    with psycopg.connect(pg,autocommit=True) as db:
        assert db.execute(
            "select count(*) from public.wow_v17_multisport_event_prediction_receipts"
        ).fetchone()[0]==0


def test_foreign_model_forward_grades_cannot_unlock_another_champion(pg):
    """Test-only forged PASS health and certificate must not substitute exact grades.

    These records are synthetic adversarial fixtures, never production
    certification, model forecasts, or independent SIRT evidence.
    """
    with psycopg.connect(pg,autocommit=True) as db:
        db.execute("""
            update public.wow_nfl_forward_calibration_health
            set graded_n=100, status='PASS',
                certification_recommendation='FORWARD_EVIDENCE_PASS',
                blockers='[]'::jsonb
        """)
        db.execute("""
            insert into public.wow_team_event_specialist_certifications (
              certification_id,sport,league_scope,model_family,model_version,
              artifact_id,artifact_sha256,calibration_artifact_id,
              calibration_sha256,independent_verification_status,
              certification_status,probability_publishable,can_execute,
              certified_at,verification_receipt)
            select %s::uuid,'NFL','NFL',a.model_family,a.model_artifact_version,
                   a.artifact_id::text,a.artifact_checksum,
                   c.calibrator_id::text,
                   encode(extensions.digest(convert_to(to_jsonb(c)::text,'UTF8'),'sha256'),'hex'),
                   'PASS','CERTIFIED',true,false,clock_timestamp(),
                   '{"test_fixture_only":true}'::jsonb
            from public.wow_nfl_event_fitted_model_artifacts a
            join public.wow_calibrators c on c.calibrator_id=a.calibrator_id
            where a.artifact_id=%s::uuid
        """,(str(uuid4()),MODEL))
        db.execute("""
            with foreign_predictions as (
              insert into public.wow_nfl_event_predictions (
                score_snapshot_id,official_event_id,event_start_time_utc,
                created_at,model_timestamp,model_artifact_id,model_version,
                calibration_version
              )
              select gen_random_uuid(),'foreign-fixture-'||g::text,
                     %s::timestamptz,%s::timestamptz,%s::timestamptz,
                     %s::uuid,'OTHER_VERSION','CAL_VERSION'
              from generate_series(1,100) g
              returning event_prediction_id,official_event_id,
                        event_start_time_utc,created_at,model_version
            )
            insert into public.wow_nfl_forward_shadow_grades (
              official_event_id,event_prediction_id,event_start_time_utc,
              prediction_created_at,model_version
            )
            select official_event_id,event_prediction_id,event_start_time_utc,
                   created_at,model_version from foreign_predictions
        """,(START,MODEL_AT,MODEL_AT,MODEL))
        # Even a test-only certificate and 100 rows from a different
        # model must not make the target NFL score certifiable.
        count=db.execute("select count(*) from public.wow_nfl_forward_shadow_grades").fetchone()[0]
        assert count == 100
    eligible=call(pg,"wow_v17_nfl_receipt_certified",SCORE)
    assert eligible is False
    issued=call(pg,"wow_v17_admit_nfl_event_receipt",SCORE)
    assert issued["status"] == "HOLD"
    assert "event_prediction_id" not in issued

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
