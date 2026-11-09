"""Real PostgreSQL proof for the reducer-owned NFL receipt producer.

This exercises the private origin ledger, exactly-once issuance, stale-receipt
fail-closed behaviour, tamper-resistant attestation, and role isolation.

It does NOT prove an end-to-end published probability: the admission candidate
independently requires 100 genuine forward grades AND a separately certified
specialist, and neither is manufactured here. A disposable PostgreSQL instance
is not independent SIRT acceptance.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

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

from psycopg.types.json import Jsonb

SQL = (
    Path(__file__).resolve().parents[1] / "v17" / "sql_candidates"
    / "20261106_nfl_reducer_owned_receipt_UNDEPLOYED.sql"
)
TABLES = [
    "public.wow_nfl_event_predictions",
    "public.wow_nfl_event_fitted_model_artifacts",
    "public.wow_calibrators",
]


@pytest.fixture(scope="module")
def pg(tmp_path_factory):
    ci_dsn = os.environ.get("WOW_V17_POSTGRES_TEST_DSN")
    if not ci_dsn and os.environ.get("GITHUB_ACTIONS") == "true":
        # Hosted regression-shards provisions PostgreSQL 16 on localhost.
        # Mirror the established multisport admission fixture: never turn
        # required hosted DB acceptance into a skip or a fake pgserver pass.
        service_dsn = "postgresql://wow_agent_ci:wow_agent_ci@localhost:5432/wow_agent_ci"
        try:
            with psycopg.connect(service_dsn, connect_timeout=2):
                ci_dsn = service_dsn
        except psycopg.OperationalError:
            if REQUIRE_POSTGRES:
                pytest.fail("REQUIRED_LOCAL_POSTGRES_SERVICE_UNAVAILABLE")
    we_created_db = False
    test_db = None
    if ci_dsn:
        from urllib.parse import urlsplit, urlunsplit
        from uuid import uuid4

        original = urlsplit(ci_dsn)
        test_db = "wow_v17_reducer_receipt_" + uuid4().hex[:16]
        with psycopg.connect(ci_dsn, autocommit=True) as admin:
            admin.execute("CREATE DATABASE " + test_db)
        uri = urlunsplit((original.scheme, original.netloc, "/" + test_db, "", ""))
        we_created_db = True
    elif pgserver is not None:
        server = pgserver.get_server(
            str(tmp_path_factory.mktemp("reducer-receipt-producer"))
        )
        uri = server.get_uri()
    elif REQUIRE_POSTGRES:
        pytest.fail("POSTGRES_TEST_SERVER_UNAVAILABLE")
    else:
        pytest.skip("No test PostgreSQL available: use hosted service or local pgserver")

    try:
        with psycopg.connect(uri, autocommit=True) as db:
            # The producer/attestor uses core pg_catalog.sha256, so no
            # contrib extension is required for this trust-root proof.
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
            db.execute("""
                create table public.wow_nfl_event_predictions (
                  score_snapshot_id uuid primary key,
                  event_prediction_id uuid unique default gen_random_uuid(),
                  official_event_id text,
                  event_start_time_utc timestamptz,
                  home_team text, away_team text, selected_participant text,
                  model_artifact_id uuid, model_version text,
                  latest_material_update_timestamp timestamptz,
                  model_timestamp timestamptz,
                  model_probability_publishable boolean,
                  model_package_valid boolean,
                  provenance_complete boolean,
                  calibration_health_status text,
                  blend_publishable boolean,
                  can_execute boolean
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
            model = "11111111-1111-4111-8111-111111111111"
            calibrator = "22222222-2222-4222-8222-222222222222"
            db.execute("""
                insert into public.wow_calibrators
                (calibrator_id,calibration_version,active,promoted,
                 validation_status,health_status,sport,market_family)
                values (%s,'CAL_VERSION',true,true,'PASS','PASS','NFL','OUTRIGHT_WINNER')
            """, (calibrator,))
            db.execute("""
                insert into public.wow_nfl_event_fitted_model_artifacts
                (artifact_id,model_artifact_version,artifact_checksum,calibrator_id,
                 model_family,lifecycle_state,active,promoted,can_execute,
                 probability_publishable)
                values (%s,'NFL_VERSION',%s,%s,'NFL_OUTRIGHT_WIN_LOGREG_V1',
                        'CHAMPION',true,true,false,false)
            """, (model, "a" * 64, calibrator))
            db.execute(SQL.read_text(encoding="utf-8"))
        yield uri
    finally:
        if we_created_db and test_db:
            with psycopg.connect(ci_dsn, autocommit=True) as admin:
                admin.execute("DROP DATABASE " + test_db + " WITH (FORCE)")


@pytest.fixture
def score(pg):
    """A PASS-eligible terminal reduction that has not yet been issued."""
    from uuid import uuid4

    sid = str(uuid4())
    start = datetime.now(timezone.utc) + timedelta(days=2)
    model_at = datetime.now(timezone.utc) - timedelta(hours=1)
    with psycopg.connect(pg, autocommit=True) as db:
        db.execute("""
            insert into public.wow_nfl_event_predictions (
              score_snapshot_id,official_event_id,event_start_time_utc,
              home_team,away_team,selected_participant,model_artifact_id,
              model_version,latest_material_update_timestamp,model_timestamp,
              model_probability_publishable,model_package_valid,
              provenance_complete,calibration_health_status,
              blend_publishable,can_execute
            ) values (%s,%s,%s,'H','A','H',
              '11111111-1111-4111-8111-111111111111','NFL_VERSION',
              null,%s,true,true,true,'PASS',false,false)
        """, (sid, "fixture-" + sid, start, model_at))
    return sid


def call(uri, function, *args):
    placeholders = ",".join(["%s"] * len(args))
    with psycopg.connect(uri, autocommit=True) as db:
        return db.execute(
            f"select public.{function}({placeholders})", args
        ).fetchone()[0]


def attest(uri, score_id, receipt_id, digest):
    with psycopg.connect(uri, autocommit=True) as db:
        return db.execute(
            "select wow_terminal_reducer.verify_nfl_reducer_receipt(%s,%s,%s)",
            (score_id, receipt_id, digest),
        ).fetchone()[0]


def ledger_count(uri, score_id):
    with psycopg.connect(uri, autocommit=True) as db:
        return db.execute(
            "select count(*) from wow_terminal_reducer.origin_receipts "
            "where score_snapshot_id=%s", (score_id,)
        ).fetchone()[0]


def test_pass_emits_authenticated_reducer_receipt(pg, score):
    import re

    reduction = call(pg, "wow_v17_reduce_nfl_event_terminal_label", score)
    assert reduction["status"] == "PASS"
    assert reduction["terminal_label"] == "FINAL_APPROVED"
    assert reduction["global_terminal_reducer"] == "V17_TERMINAL_REDUCER"
    assert reduction["probability_publishable"] is True
    assert reduction["rank_eligible"] is True
    assert reduction["can_execute"] is False
    assert re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        reduction["reducer_receipt_id"],
    )
    assert re.fullmatch(r"[0-9a-f]{64}", reduction["reducer_receipt_sha256"])
    assert attest(
        pg, score, reduction["reducer_receipt_id"], reduction["reducer_receipt_sha256"]
    ) is True
    assert ledger_count(pg, score) == 1


def test_receipt_identity_is_stable_and_exactly_once(pg, score):
    first = call(pg, "wow_v17_reduce_nfl_event_terminal_label", score)
    second = call(pg, "wow_v17_reduce_nfl_event_terminal_label", score)
    assert first["reducer_receipt_id"] == second["reducer_receipt_id"]
    assert first["reducer_receipt_sha256"] == second["reducer_receipt_sha256"]
    assert ledger_count(pg, score) == 1


def test_attestor_binds_exact_score_receipt_and_digest(pg, score):
    from uuid import uuid4

    first = call(pg, "wow_v17_reduce_nfl_event_terminal_label", score)
    rid, digest = first["reducer_receipt_id"], first["reducer_receipt_sha256"]
    assert attest(pg, score, rid, digest) is True
    assert attest(pg, score, str(uuid4()), digest) is False
    assert attest(pg, score, rid, "b" * 64) is False
    assert attest(pg, str(uuid4()), rid, digest) is False
    assert attest(pg, score, rid, digest.upper()) is False


def test_hold_never_mints_a_receipt(pg):
    from uuid import uuid4

    sid = str(uuid4())
    with psycopg.connect(pg, autocommit=True) as db:
        db.execute("""
            insert into public.wow_nfl_event_predictions (
              score_snapshot_id,official_event_id,event_start_time_utc,
              home_team,away_team,selected_participant,model_artifact_id,
              model_version,model_timestamp,model_probability_publishable,
              model_package_valid,provenance_complete,calibration_health_status,
              blend_publishable,can_execute
            ) values (%s,%s,%s,'H','A','H',
              '11111111-1111-4111-8111-111111111111','NFL_VERSION',%s,
              true,true,true,'PASS',false,false)
        """, (
            sid, "past-" + sid,
            datetime.now(timezone.utc) - timedelta(hours=1),
            datetime.now(timezone.utc) - timedelta(hours=2),
        ))
    reduction = call(pg, "wow_v17_reduce_nfl_event_terminal_label", sid)
    assert reduction["status"] == "HOLD"
    assert "EVENT_NOT_PREGAME" in reduction["blockers"]
    assert "reducer_receipt_id" not in reduction
    assert "reducer_receipt_sha256" not in reduction
    assert ledger_count(pg, sid) == 0


def test_material_change_after_issuance_fails_closed_without_reminting(pg, score):
    issued = call(pg, "wow_v17_reduce_nfl_event_terminal_label", score)
    with psycopg.connect(pg, autocommit=True) as db:
        db.execute(
            "update public.wow_nfl_event_predictions set provenance_complete=false "
            "where score_snapshot_id=%s", (score,),
        )
    stale = call(pg, "wow_v17_reduce_nfl_event_terminal_label", score)
    assert stale["status"] == "HOLD"
    assert "PROVENANCE_INCOMPLETE" in stale["blockers"]
    assert "reducer_receipt_id" not in stale
    assert ledger_count(pg, score) == 1
    with psycopg.connect(pg, autocommit=True) as db:
        row = db.execute(
            "select reducer_receipt_id,reducer_receipt_sha256 "
            "from wow_terminal_reducer.origin_receipts where score_snapshot_id=%s",
            (score,),
        ).fetchone()
    assert str(row[0]) == issued["reducer_receipt_id"]
    assert row[1] == issued["reducer_receipt_sha256"]
    # Restoring the exact prior state must re-authenticate the same receipt.
    with psycopg.connect(pg, autocommit=True) as db:
        db.execute(
            "update public.wow_nfl_event_predictions set provenance_complete=true "
            "where score_snapshot_id=%s", (score,),
        )
    restored = call(pg, "wow_v17_reduce_nfl_event_terminal_label", score)
    assert restored["status"] == "PASS"
    assert restored["reducer_receipt_id"] == issued["reducer_receipt_id"]
    assert restored["reducer_receipt_sha256"] == issued["reducer_receipt_sha256"]
    assert ledger_count(pg, score) == 1


def test_issuer_refuses_direct_stale_rebinding(pg, score):
    issued = call(pg, "wow_v17_reduce_nfl_event_terminal_label", score)
    forged = dict(issued)
    forged.pop("reducer_receipt_id")
    forged.pop("reducer_receipt_sha256")
    forged["blockers"] = ["DIFFERENT_PAYLOAD"]
    with psycopg.connect(pg, autocommit=True) as db:
        result = db.execute(
            "select wow_terminal_reducer.issue_nfl_reducer_receipt(%s,%s)",
            (score, Jsonb(forged)),
        ).fetchone()[0]
    assert result is None
    assert ledger_count(pg, score) == 1


def test_issuer_rejects_hold_and_preminted_payloads(pg, score):
    issued = call(pg, "wow_v17_reduce_nfl_event_terminal_label", score)
    hold = {"status": "HOLD", "terminal_label": "MODEL_QUALIFIED_HOLD",
            "global_terminal_reducer": "V17_TERMINAL_REDUCER",
            "probability_publishable": False, "rank_eligible": False,
            "can_execute": False}
    with psycopg.connect(pg, autocommit=True) as db:
        assert db.execute(
            "select wow_terminal_reducer.issue_nfl_reducer_receipt(%s,%s)",
            (score, Jsonb(hold)),
        ).fetchone()[0] is None
        preminted = dict(issued)
        assert db.execute(
            "select wow_terminal_reducer.issue_nfl_reducer_receipt(%s,%s)",
            (score, Jsonb(preminted)),
        ).fetchone()[0] is None


def test_origin_ledger_is_immutable(pg, score):
    call(pg, "wow_v17_reduce_nfl_event_terminal_label", score)
    for statement in (
        "update wow_terminal_reducer.origin_receipts set issuer='X'",
        "delete from wow_terminal_reducer.origin_receipts",
        "truncate wow_terminal_reducer.origin_receipts",
    ):
        with psycopg.connect(pg, autocommit=True) as db:
            with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
                db.execute(statement)
    assert ledger_count(pg, score) == 1


def test_tampered_ledger_digest_never_attests(pg, score):
    from uuid import uuid4

    issued = call(pg, "wow_v17_reduce_nfl_event_terminal_label", score)
    payload = {k: issued[k] for k in (
        "status", "terminal_label", "blockers", "global_terminal_reducer",
        "probability_publishable", "rank_eligible", "can_execute",
    )}
    other_score, other_id = str(uuid4()), str(uuid4())
    with psycopg.connect(pg, autocommit=True) as db:
        db.execute("""
            insert into wow_terminal_reducer.origin_receipts
            (score_snapshot_id,reducer_receipt_id,reducer_receipt_sha256,
             reduction_payload)
            values (%s,%s,%s,%s)
        """, (other_score, other_id, "d" * 64, Jsonb(payload)))
    # Correct shape, wrong bound digest: recomputation must reject it.
    assert attest(pg, other_score, other_id, "d" * 64) is False


def test_private_origin_is_inaccessible_to_application_roles(pg, score):
    call(pg, "wow_v17_reduce_nfl_event_terminal_label", score)
    for role in ("anon", "authenticated", "service_role"):
        with psycopg.connect(pg, autocommit=True) as db:
            assert db.execute(
                "select pg_catalog.has_schema_privilege(%s,'wow_terminal_reducer','USAGE')",
                (role,),
            ).fetchone()[0] is False
            for signature in (
                "wow_terminal_reducer.issue_nfl_reducer_receipt(uuid,jsonb)",
                "wow_terminal_reducer.verify_nfl_reducer_receipt(uuid,uuid,text)",
                "wow_terminal_reducer.nfl_receipt_digest(uuid,jsonb)",
            ):
                assert db.execute(
                    "select pg_catalog.has_function_privilege(%s,%s,'EXECUTE')",
                    (role, signature),
                ).fetchone()[0] is False
            db.execute(f"set role {role}")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                db.execute(
                    "select count(*) from wow_terminal_reducer.origin_receipts"
                )


def test_only_service_role_reaches_the_public_reducer(pg, score):
    with psycopg.connect(pg, autocommit=True) as db:
        assert db.execute(
            "select pg_catalog.has_function_privilege("
            "'service_role','public.wow_v17_reduce_nfl_event_terminal_label(uuid)',"
            "'EXECUTE')"
        ).fetchone()[0] is True
        for role in ("anon", "authenticated"):
            assert db.execute(
                "select pg_catalog.has_function_privilege("
                "%s,'public.wow_v17_reduce_nfl_event_terminal_label(uuid)',"
                "'EXECUTE')", (role,),
            ).fetchone()[0] is False
    with psycopg.connect(pg, autocommit=True) as db:
        db.execute("set role service_role")
        reduction = db.execute(
            "select public.wow_v17_reduce_nfl_event_terminal_label(%s)", (score,)
        ).fetchone()[0]
    assert reduction["status"] == "PASS"
    assert "reducer_receipt_sha256" in reduction
    assert reduction["can_execute"] is False


def test_receipt_digest_binds_the_source_score(pg, score):
    from uuid import uuid4

    first = call(pg, "wow_v17_reduce_nfl_event_terminal_label", score)
    sid = str(uuid4())
    with psycopg.connect(pg, autocommit=True) as db:
        db.execute("""
            insert into public.wow_nfl_event_predictions (
              score_snapshot_id,official_event_id,event_start_time_utc,
              home_team,away_team,selected_participant,model_artifact_id,
              model_version,model_timestamp,model_probability_publishable,
              model_package_valid,provenance_complete,calibration_health_status,
              blend_publishable,can_execute
            ) values (%s,%s,%s,'H','A','H',
              '11111111-1111-4111-8111-111111111111','NFL_VERSION',%s,
              true,true,true,'PASS',false,false)
        """, (
            sid, "twin-" + sid,
            datetime.now(timezone.utc) + timedelta(days=2),
            datetime.now(timezone.utc) - timedelta(hours=1),
        ))
    second = call(pg, "wow_v17_reduce_nfl_event_terminal_label", sid)
    assert second["status"] == "PASS"
    assert second["reducer_receipt_id"] != first["reducer_receipt_id"]
    assert second["reducer_receipt_sha256"] != first["reducer_receipt_sha256"]
    assert attest(pg, sid, first["reducer_receipt_id"],
                  first["reducer_receipt_sha256"]) is False
