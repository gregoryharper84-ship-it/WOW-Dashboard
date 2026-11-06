"""End-to-end PostgreSQL proof: real reducer producer -> admission -> readback.

Both undeployed SQL candidates are installed in one disposable database, and
the real `wow_v17_reduce_nfl_event_terminal_label` mints the receipt that
`wow_v17_admit_nfl_event_receipt` consumes. No mocked reducer or attestor is
substituted for the producer.

The 100-row forward cohort, PASS health row and specialist certificate are
`test_fixture_only` plumbing inputs, exactly as in the existing admission
suite. They are NOT independent SIRT certification, real NFL outcomes, or
permission to publish. This test proves composition and fail-closed wiring,
not sporting qualification.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import os
import pytest

REQUIRE_POSTGRES = (
    os.environ.get("WOW_V17_REQUIRE_POSTGRES_RECEIPT_TESTS") == "1"
    or (
        os.environ.get("GITHUB_ACTIONS") == "true"
        and os.environ.get("GITHUB_JOB") == "regression-shards"
    )
)
if REQUIRE_POSTGRES:
    import psycopg
else:
    psycopg = pytest.importorskip("psycopg")
try:
    import pgserver
except ImportError:
    pgserver = None

ENGINE = Path(__file__).resolve().parents[1]
PRODUCER = (
    ENGINE / "v17/sql_candidates/20261106_nfl_reducer_owned_receipt_UNDEPLOYED.sql"
)
ADMISSION = (
    ENGINE / "v17/sql_candidates/20261105_multisport_nfl_receipt_admission_UNDEPLOYED.sql"
)
MODEL = "11111111-1111-4111-8111-111111111111"
CALIBRATOR = "22222222-2222-4222-8222-222222222222"
HASH = "a" * 64


def _fixture_now():
    now = datetime.now(timezone.utc)
    return now + timedelta(days=2), now - timedelta(hours=2), now - timedelta(hours=1)


@pytest.fixture(scope="module")
def pg(tmp_path_factory):
    start, snap_at, model_at = _fixture_now()
    ci_dsn = os.environ.get("WOW_V17_POSTGRES_TEST_DSN")
    created_db = None
    if ci_dsn:
        original = urlsplit(ci_dsn)
        if original.scheme not in {"postgres", "postgresql"} or not original.hostname:
            pytest.fail("POSTGRES_CI_DSN_INVALID")
        created_db = "wow_v17_e2e_receipt_" + uuid4().hex[:16]
        with psycopg.connect(ci_dsn, autocommit=True) as admin:
            admin.execute("CREATE DATABASE " + created_db)
        uri = urlunsplit(
            (original.scheme, original.netloc, "/" + created_db, "", "")
        )
    elif pgserver is not None:
        server = pgserver.get_server(
            str(tmp_path_factory.mktemp("reducer-receipt-e2e"))
        )
        uri = server.get_uri()
    elif REQUIRE_POSTGRES:
        pytest.fail("POSTGRES_TEST_SERVER_UNAVAILABLE")
    else:
        pytest.skip("No test PostgreSQL available: use hosted service or local pgserver")

    try:
        with psycopg.connect(uri, autocommit=True) as db:
            db.execute("create schema if not exists extensions")
            try:
                db.execute(
                    "create extension if not exists pgcrypto with schema extensions"
                )
            except (psycopg.errors.UndefinedFile, psycopg.errors.FeatureNotSupported):
                # Local pgserver ships PostgreSQL without contrib extensions.
                # Core sha256 is the same function pgcrypto exposes for the
                # only algorithm this candidate uses. CI must never take this
                # branch: a missing pgcrypto there is a real environment gap.
                if REQUIRE_POSTGRES:
                    raise
                db.execute("""
                    create or replace function extensions.digest(
                      p_data bytea, p_type text
                    ) returns bytea language sql immutable set search_path='' as $d$
                      select case lower(p_type)
                        when 'sha256' then pg_catalog.sha256(p_data)
                        else null end
                    $d$
                """)
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
            db.execute(
                "grant usage on schema public to service_role, anon, authenticated"
            )
            db.execute("""
                create table public.wow_nfl_event_predictions (
                  score_snapshot_id uuid primary key,
                  event_prediction_id uuid unique default gen_random_uuid(),
                  created_at timestamptz default clock_timestamp(),
                  official_event_id text,
                  event_start_time_utc timestamptz,
                  home_team text, away_team text, selected_participant text,
                  source_snapshot_id uuid, source_snapshot_timestamp timestamptz,
                  model_timestamp timestamptz, model_artifact_id uuid,
                  model_version text, model_output_snapshot jsonb,
                  latest_material_update_timestamp timestamptz,
                  calibration_version text, model_probability_publishable boolean,
                  model_package_valid boolean, provenance_complete boolean,
                  calibration_health_status text, blend_publishable boolean,
                  can_execute boolean, calibrated_home_probability numeric,
                  calibrated_home_lower_bound numeric,
                  calibrated_home_upper_bound numeric,
                  calibrated_away_probability numeric,
                  calibrated_away_lower_bound numeric,
                  calibrated_away_upper_bound numeric
                )
            """)
            db.execute("""
                create table public.wow_nfl_event_fitted_model_artifacts (
                  artifact_id uuid primary key, model_artifact_version text,
                  artifact_checksum text, calibrator_id uuid, model_family text,
                  lifecycle_state text, active boolean, promoted boolean,
                  can_execute boolean,
                  probability_publishable boolean not null default false
                    constraint wow_nfl_event_fitted_registry_not_probability_publication
                    check (probability_publishable=false)
                )
            """)
            db.execute("""
                create table public.wow_calibrators (
                  calibrator_id uuid primary key, calibration_version text,
                  active boolean, promoted boolean, validation_status text,
                  health_status text, sport text, market_family text
                )
            """)
            db.execute("""
                create table public.wow_nfl_forward_shadow_grades (
                  official_event_id text, event_prediction_id uuid,
                  event_start_time_utc timestamptz,
                  prediction_created_at timestamptz, model_version text
                )
            """)
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
                  certification_id uuid primary key, sport text,
                  league_scope text, model_family text, model_version text,
                  artifact_id text, artifact_sha256 text,
                  calibration_artifact_id text, calibration_sha256 text,
                  independent_verification_status text,
                  certification_status text, probability_publishable boolean,
                  can_execute boolean, certified_at timestamptz,
                  verification_receipt jsonb, supersedes_certification_id uuid
                )
            """)
            db.execute("""
                insert into public.wow_calibrators
                (calibrator_id,calibration_version,active,promoted,
                 validation_status,health_status,sport,market_family)
                values (%s,'CAL_VERSION',true,true,'PASS','PASS','NFL','OUTRIGHT_WINNER')
            """, (CALIBRATOR,))
            db.execute("""
                insert into public.wow_nfl_event_fitted_model_artifacts
                (artifact_id,model_artifact_version,artifact_checksum,calibrator_id,
                 model_family,lifecycle_state,active,promoted,can_execute,
                 probability_publishable)
                values (%s,'NFL_VERSION',%s,%s,'NFL_OUTRIGHT_WIN_LOGREG_V1',
                        'CHAMPION',true,true,false,false)
            """, (MODEL, HASH, CALIBRATOR))
            # Install the reducer producer first, then the consumer candidate.
            db.execute(PRODUCER.read_text(encoding="utf-8"))
            db.execute(ADMISSION.read_text(encoding="utf-8"))
            stage_test_only_certified_cohort(db, start, model_at)
        yield uri
    finally:
        if created_db:
            with psycopg.connect(ci_dsn, autocommit=True) as admin:
                admin.execute("DROP DATABASE " + created_db + " WITH (FORCE)")


def stage_test_only_certified_cohort(db, start, model_at):
    """Plumbing-only inputs, explicitly rejected as independent certification."""
    db.execute("""
        with cohort as (
          insert into public.wow_nfl_event_predictions (
            score_snapshot_id,official_event_id,event_start_time_utc,created_at,
            model_timestamp,model_artifact_id,model_version,calibration_version
          )
          select gen_random_uuid(),'cohort-fixture-'||g::text,
                 %s::timestamptz,%s::timestamptz,%s::timestamptz,
                 %s::uuid,'NFL_VERSION','CAL_VERSION'
          from generate_series(1,100) g
          returning event_prediction_id,official_event_id,event_start_time_utc,
                    created_at,model_version
        )
        insert into public.wow_nfl_forward_shadow_grades (
          official_event_id,event_prediction_id,event_start_time_utc,
          prediction_created_at,model_version
        )
        select official_event_id,event_prediction_id,event_start_time_utc,
               created_at,model_version from cohort
    """, (start, model_at, model_at, MODEL))
    db.execute("""
        insert into public.wow_nfl_forward_calibration_health
        (generated_at,created_at,graded_n,minimum_forward_required,status,
         certification_recommendation,blockers,can_execute)
        values (clock_timestamp(),clock_timestamp(),100,100,'PASS',
                'FORWARD_EVIDENCE_PASS','[]'::jsonb,false)
    """)
    db.execute("""
        insert into public.wow_team_event_specialist_certifications (
          certification_id,sport,league_scope,model_family,model_version,
          artifact_id,artifact_sha256,calibration_artifact_id,calibration_sha256,
          independent_verification_status,certification_status,
          probability_publishable,can_execute,certified_at,verification_receipt)
        select %s::uuid,'NFL','NFL',a.model_family,a.model_artifact_version,
               a.artifact_id::text,a.artifact_checksum,c.calibrator_id::text,
               encode(extensions.digest(
                 convert_to(to_jsonb(c)::text,'UTF8'),'sha256'),'hex'),
               'PASS','CERTIFIED',true,false,clock_timestamp(),
               '{"test_fixture_only":true}'::jsonb
        from public.wow_nfl_event_fitted_model_artifacts a
        join public.wow_calibrators c on c.calibrator_id=a.calibrator_id
        where a.artifact_id=%s::uuid
    """, (str(uuid4()), MODEL))


@pytest.fixture(autouse=True)
def reset_between_cases(pg):
    with psycopg.connect(pg, autocommit=True) as db:
        db.execute(
            "alter table wow_terminal_reducer.origin_receipts "
            "disable trigger user"
        )
        db.execute("truncate wow_terminal_reducer.origin_receipts")
        db.execute(
            "alter table wow_terminal_reducer.origin_receipts "
            "enable trigger user"
        )
        db.execute("truncate public.wow_v17_multisport_event_prediction_receipts")
        db.execute("""
            update public.wow_nfl_forward_calibration_health
            set graded_n=100,status='PASS',
                certification_recommendation='FORWARD_EVIDENCE_PASS',
                blockers='[]'::jsonb
        """)
    yield


@pytest.fixture
def score(pg):
    start, snap_at, model_at = _fixture_now()
    sid = str(uuid4())
    with psycopg.connect(pg, autocommit=True) as db:
        db.execute("""
            insert into public.wow_nfl_event_predictions (
              score_snapshot_id,official_event_id,event_start_time_utc,
              home_team,away_team,selected_participant,source_snapshot_id,
              source_snapshot_timestamp,model_timestamp,model_artifact_id,
              model_version,model_output_snapshot,calibration_version,
              model_probability_publishable,model_package_valid,
              provenance_complete,calibration_health_status,blend_publishable,
              can_execute,calibrated_home_probability,
              calibrated_home_lower_bound,calibrated_home_upper_bound,
              calibrated_away_probability,calibrated_away_lower_bound,
              calibrated_away_upper_bound
            ) values (%s,%s,%s,'H','A','H',%s,%s,%s,%s,'NFL_VERSION',
              jsonb_build_object('artifact_checksum',%s::text),'CAL_VERSION',
              true,true,true,'PASS',false,false,.61,.55,.65,.39,.35,.45)
        """, (
            sid, "score-" + sid, start, str(uuid4()), snap_at, model_at,
            MODEL, HASH,
        ))
    return sid


def call(uri, function, *args):
    placeholders = ",".join(["%s"] * len(args))
    with psycopg.connect(uri, autocommit=True) as db:
        return db.execute(
            f"select public.{function}({placeholders})", args
        ).fetchone()[0]


def count(uri, table, where, value):
    with psycopg.connect(uri, autocommit=True) as db:
        return db.execute(
            f"select count(*) from {table} where {where}=%s", (value,)
        ).fetchone()[0]


def test_real_producer_is_admitted_and_reads_back(pg, score):
    reduction = call(pg, "wow_v17_reduce_nfl_event_terminal_label", score)
    assert reduction["status"] == "PASS"
    assert reduction["reducer_receipt_id"] and reduction["reducer_receipt_sha256"]
    issued = call(pg, "wow_v17_admit_nfl_event_receipt", score)
    assert issued["status"] == "PERSISTED"
    assert issued["can_execute"] is False
    assert issued["reducer_receipt_id"] == reduction["reducer_receipt_id"]
    assert issued["reducer_receipt_sha256"] == reduction["reducer_receipt_sha256"]
    assert count(
        pg, "wow_terminal_reducer.origin_receipts", "score_snapshot_id", score
    ) == 1
    verified = call(
        pg, "wow_v17_verify_multisport_event_receipt",
        issued["event_prediction_id"],
    )
    assert verified["status"] == "PASS"
    assert verified["terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert verified["reducer_receipt_id"] == reduction["reducer_receipt_id"]
    assert verified["reducer_receipt_sha256"] == reduction["reducer_receipt_sha256"]
    assert verified["can_execute"] is False
    assert verified["probability_publishable"] is True
    assert verified["rank_eligible"] is True


def test_second_admission_is_already_persisted_with_same_receipt(pg, score):
    issued = call(pg, "wow_v17_admit_nfl_event_receipt", score)
    assert issued["status"] == "PERSISTED"
    repeated = call(pg, "wow_v17_admit_nfl_event_receipt", score)
    assert repeated["status"] == "ALREADY_PERSISTED"
    assert repeated["event_prediction_id"] == issued["event_prediction_id"]
    assert repeated["reducer_receipt_id"] == issued["reducer_receipt_id"]
    assert repeated["reducer_receipt_sha256"] == issued["reducer_receipt_sha256"]
    assert count(
        pg, "wow_v17_multisport_event_prediction_receipts",
        "score_snapshot_id", score,
    ) == 1


def test_missing_independent_attestor_holds_the_composed_admission(pg, score):
    with psycopg.connect(pg) as db:
        db.execute(
            "drop function "
            "wow_terminal_reducer.verify_nfl_reducer_receipt(uuid,uuid,text)"
        )
        held = db.execute(
            "select public.wow_v17_admit_nfl_event_receipt(%s::uuid)", (score,)
        ).fetchone()[0]
        assert held["status"] == "HOLD"
        assert held["code"] == "LLP_REDUCER_ORIGIN_RECEIPT_UNAVAILABLE"
        assert held["can_execute"] is False
        db.rollback()
    assert count(
        pg, "wow_v17_multisport_event_prediction_receipts",
        "score_snapshot_id", score,
    ) == 0


def test_material_update_after_admission_revokes_readback(pg, score):
    issued = call(pg, "wow_v17_admit_nfl_event_receipt", score)
    assert issued["status"] == "PERSISTED"
    with psycopg.connect(pg, autocommit=True) as db:
        db.execute(
            "update public.wow_nfl_event_predictions "
            "set latest_material_update_timestamp=%s where score_snapshot_id=%s",
            (datetime.now(timezone.utc), score),
        )
    assert call(pg, "wow_v17_reduce_nfl_event_terminal_label", score)["status"] == "HOLD"
    verified = call(
        pg, "wow_v17_verify_multisport_event_receipt",
        issued["event_prediction_id"],
    )
    assert verified["status"] == "HOLD"
    assert verified["can_execute"] is False


def test_hold_never_admits_and_never_mints(pg, score):
    with psycopg.connect(pg, autocommit=True) as db:
        db.execute(
            "update public.wow_nfl_event_predictions set provenance_complete=false "
            "where score_snapshot_id=%s", (score,),
        )
    held = call(pg, "wow_v17_admit_nfl_event_receipt", score)
    assert held["status"] == "HOLD"
    assert "event_prediction_id" not in held
    assert held["can_execute"] is False
    assert count(
        pg, "wow_terminal_reducer.origin_receipts", "score_snapshot_id", score
    ) == 0
    assert count(
        pg, "wow_v17_multisport_event_prediction_receipts",
        "score_snapshot_id", score,
    ) == 0


def test_service_role_reaches_admission_but_not_private_origin(pg, score):
    with psycopg.connect(pg, autocommit=True) as db:
        assert db.execute(
            "select pg_catalog.has_function_privilege("
            "'service_role','public.wow_v17_admit_nfl_event_receipt(uuid)',"
            "'EXECUTE')"
        ).fetchone()[0] is True
        db.execute("set role service_role")
        issued = db.execute(
            "select public.wow_v17_admit_nfl_event_receipt(%s::uuid)", (score,)
        ).fetchone()[0]
        assert issued["status"] == "PERSISTED"
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            db.execute("select count(*) from wow_terminal_reducer.origin_receipts")
