"""Database-backed proof for the NBA/WNBA certified-calibrator lookup.

Source-string assertions and a parse-only SQL lint are not evidence that the
migration and its security contract work. This module applies the real
dependency-ordered DDL to a real PostgreSQL server, seeds real rows, and calls
the function as each role.

Two supported targets:

* ``WOW_TEST_POSTGRES_DSN`` -- an authorized disposable database (CI service
  container or a scratch project). Roles ``anon``, ``authenticated`` and
  ``service_role`` must exist, as they do in Supabase.
* the local ``pgserver`` package -- a throwaway PostgreSQL cluster started by the
  test. Used here when the package and ``psycopg`` are installed.

When neither is available the module skips with an explicit reason; it never
reports a pass without touching a database.
"""
from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

import pytest

psycopg = pytest.importorskip(
    "psycopg", reason="psycopg is required for database-backed migration proof"
)
from psycopg.rows import dict_row  # noqa: E402
from psycopg.types.json import Jsonb  # noqa: E402

from v17.multisport_team_event_calibration import calibration_payload_sha256  # noqa: E402

ENGINE_DIR = Path(__file__).resolve().parents[1]
MIGRATIONS = ENGINE_DIR / "migrations"

IMMUTABILITY_SQL = MIGRATIONS / "20260915_harden_immutable_mutation_trigger_search_path.sql"
BASKETBALL_SQL = MIGRATIONS / "20260913_basketball_team_event_specialists.sql"
CERTIFICATION_SQL = MIGRATIONS / "20260921_create_team_event_specialist_certifications.sql"
EVIDENCE_SQL = MIGRATIONS / "20261007_basketball_calibration_artifact_evidence.sql"
RPC_SQL = MIGRATIONS / "20261102_basketball_certified_calibrator_artifact_rpc.sql"

DEPENDENCY_ORDER = (IMMUTABILITY_SQL, BASKETBALL_SQL, CERTIFICATION_SQL, EVIDENCE_SQL)
RPC_SIGNATURE = "public.wow_basketball_certified_calibrator_artifact(text, uuid)"
METHOD = "PLATT_TIME_SPLIT_V1"
CONTROLLING_SPECIALIST = "NBA_GAME_WIN_PROBABILITY_EXPERT_V1"
MODEL_FAMILY = "BASKETBALL_TEAM_EVENT_LOGISTIC_V1"


def _payload(**overrides):
    payload = {
        "artifact_type": "BINARY_PLATT_CALIBRATOR",
        "sport": "NBA",
        "model_family": MODEL_FAMILY,
        "calibration_method": METHOD,
        "training_n": 240,
        "platt_a": 0.9123456789012345,
        "platt_b": -0.083,
        "residual_quantile_90": 0.08000000000000002,
        "brier_score": 0.2412345678901234,
        "log_loss": 0.6634,
        "calibration_error": 0.031,
        "source_data_hash": "a" * 64,
        "split_hash": "b" * 64,
        "fit_end": "2026-11-01T18:22:42+00:00",
        "health_status": "PASS",
        "certification_status": "PASS",
        "independent_verification_status": "REVIEW_REQUIRED",
        "bounds_preapproval_status": "REVIEW_REQUIRED",
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }
    payload.update(overrides)
    return payload


class _Cluster:
    def __init__(self, dsn: str, server=None) -> None:
        self.dsn = dsn
        self.server = server

    def close(self) -> None:
        if self.server is not None:
            self.server.cleanup()


def _start_cluster(tmp_path_factory) -> _Cluster:
    dsn = os.environ.get("WOW_TEST_POSTGRES_DSN")
    if dsn:
        return _Cluster(dsn)
    try:
        import pgserver
    except ImportError:  # pragma: no cover - environment dependent
        pytest.skip(
            "WOW_TEST_POSTGRES_UNAVAILABLE: set WOW_TEST_POSTGRES_DSN to an authorized "
            "disposable PostgreSQL database or install the pgserver test package"
        )
    path = tmp_path_factory.mktemp("pgdata")
    server = pgserver.get_server(str(path), cleanup_mode=None)
    return _Cluster(server.get_uri(), server)


def _connect(cluster: _Cluster):
    return psycopg.connect(cluster.dsn, autocommit=True, row_factory=dict_row)


def _apply(conn, path: Path) -> None:
    conn.execute(path.read_text(encoding="utf-8"))


def _ensure_roles(conn) -> None:
    conn.execute(
        """
        do $$
        begin
          if not exists (select 1 from pg_roles where rolname = 'anon') then
            create role anon nologin;
          end if;
          if not exists (select 1 from pg_roles where rolname = 'authenticated') then
            create role authenticated nologin;
          end if;
          if not exists (select 1 from pg_roles where rolname = 'service_role') then
            create role service_role nologin;
          end if;
        end $$;
        """
    )


@pytest.fixture(scope="module")
def cluster(tmp_path_factory):
    cluster = _start_cluster(tmp_path_factory)
    try:
        with _connect(cluster) as conn:
            _ensure_roles(conn)
            for path in DEPENDENCY_ORDER:
                _apply(conn, path)
            _apply(conn, RPC_SQL)
        yield cluster
    finally:
        cluster.close()


@pytest.fixture()
def conn(cluster):
    with _connect(cluster) as connection:
        yield connection


def _seed(
    conn,
    *,
    payload=None,
    digest=None,
    sport="NBA",
    method=METHOD,
    active=True,
    promoted=True,
    certify=True,
    certification_sha=None,
    legacy_status="CERTIFIED",
    certification_status="CERTIFIED",
    independent_verification_status="PASS",
    probability_publishable=True,
    revoked_after=False,
    league_scope=None,
):
    league_scope = league_scope or sport
    payload = _payload() if payload is None else payload
    calibrator_uuid = uuid.uuid4()
    calibrator_version = f"{sport}_TEAM_EVENT_PLATT_V1_{uuid.uuid4().hex[:12]}"
    model_version = f"{sport}_TEAM_EVENT_LOGISTIC_V1_{uuid.uuid4().hex[:12]}"
    artifact_id = f"nba-artifact-{uuid.uuid4().hex[:12]}"
    payload = {
        **payload,
        "sport": sport,
        "calibration_method": method,
        "calibration_version": calibrator_version,
    }
    payload_sha = calibration_payload_sha256(payload)

    conn.execute(
        """
        insert into public.wow_basketball_team_event_calibrators (
          calibrator_id, sport, calibrator_version, model_artifact_version,
          calibration_method, coefficients, calibration_row_count, brier, log_loss,
          ece, calibration_bias, promoted, active,
          calibration_artifact_payload, calibration_artifact_sha256
        ) values (%s, %s, %s, %s, %s, %s::jsonb, 240, 0.22, 0.63, 0.04, 0.01,
                  %s, %s, %s, %s)
        """,
        (
            calibrator_uuid,
            sport,
            calibrator_version,
            model_version,
            method,
            Jsonb({"a": -0.083, "b": 0.9123456789012345}),
            promoted,
            active,
            Jsonb(payload),
            digest if digest is not None else payload_sha,
        ),
    )

    # Legacy basketball capability flag. Present in production today; it must
    # never be sufficient authority on its own.
    if legacy_status is not None:
        conn.execute(
            """
            insert into public.wow_basketball_team_event_certification (
              sport, capability_status, reason_code, model_artifact_version,
              calibrator_version, corpus_rows, settled_rows, certification_notes
            ) values (%s, %s, 'CERTIFICATION_REPLAY_REQUIRED', %s, %s, 240, 240, %s::jsonb)
            on conflict (sport) do update
               set capability_status = excluded.capability_status,
                   model_artifact_version = excluded.model_artifact_version,
                   calibrator_version = excluded.calibrator_version
            """,
            (sport, legacy_status, model_version, calibrator_version, Jsonb({})),
        )

    certification_uuid = None
    if certify:
        certification_uuid = uuid.uuid4()
        conn.execute(
            """
            insert into public.wow_team_event_specialist_certifications (
              certification_id, sport, league_scope, controlling_specialist,
              model_family, model_version, artifact_id, artifact_sha256,
              feature_schema_version, input_contract_version,
              calibration_artifact_id, calibration_sha256, calibration_method,
              independent_verification_status, certification_status, certified_at,
              probability_publishable, can_execute
            ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                      clock_timestamp(), %s, false)
            """,
            (
                certification_uuid,
                sport,
                league_scope,
                CONTROLLING_SPECIALIST,
                MODEL_FAMILY,
                model_version,
                artifact_id,
                "c" * 64,
                "BASKETBALL_PREGAME_V1",
                "NBA_TEAM_EVENT_INPUT_V1",
                str(calibrator_uuid),
                certification_sha if certification_sha is not None else payload_sha,
                method,
                independent_verification_status,
                certification_status,
                probability_publishable,
            ),
        )
        if revoked_after:
            conn.execute(
                """
                insert into public.wow_team_event_specialist_certifications (
                  certification_id, created_at, sport, league_scope,
                  controlling_specialist, model_family, model_version, artifact_id,
                  artifact_sha256, feature_schema_version, input_contract_version,
                  calibration_artifact_id, calibration_sha256, calibration_method,
                  independent_verification_status, certification_status, certified_at,
                  probability_publishable, can_execute
                ) values (%s, clock_timestamp(), %s, %s, %s, %s,
                          %s, %s, %s, %s, %s, %s, %s, %s, 'FAIL', 'REVOKED',
                          clock_timestamp(), false, false)
                """,
                (
                    uuid.uuid4(),
                    sport,
                    league_scope,
                    CONTROLLING_SPECIALIST,
                    MODEL_FAMILY,
                    model_version,
                    artifact_id,
                    "c" * 64,
                    "BASKETBALL_PREGAME_V1",
                    "NBA_TEAM_EVENT_INPUT_V1",
                    str(calibrator_uuid),
                    certification_sha if certification_sha is not None else payload_sha,
                    method,
                ),
            )
    return {
        "calibrator_id": calibrator_uuid,
        "calibrator_version": calibrator_version,
        "model_version": model_version,
        "artifact_id": artifact_id,
        "payload": payload,
        "payload_sha": payload_sha,
        "certification_id": certification_uuid,
    }


def _lookup(conn, calibrator_id, *, sport="NBA", role="service_role"):
    conn.execute(f"set role {role}")
    try:
        return conn.execute(
            "select * from public.wow_basketball_certified_calibrator_artifact(%s, %s::uuid)",
            (sport, calibrator_id),
        ).fetchall()
    finally:
        conn.execute("reset role")


# ---------------------------------------------------------------------------
# Authority and identity
# ---------------------------------------------------------------------------


def test_service_role_can_execute_digest_serializer_without_public_grant(conn):
    """The normal writer's digest trigger must not hit permission denied."""
    assert conn.execute(
        "select has_function_privilege("
        "'service_role', 'public.wow_canonical_json_text(jsonb)', 'EXECUTE') as ok"
    ).fetchone()["ok"] is True
    assert conn.execute(
        "select not has_function_privilege("
        "'anon', 'public.wow_canonical_json_text(jsonb)', 'EXECUTE') as ok"
    ).fetchone()["ok"] is True
    conn.execute("set role service_role")
    try:
        result = conn.execute(
            "select public.wow_canonical_json_text(%s::jsonb) as canonical",
            (Jsonb({"z": 1, "a": 2}),),
        ).fetchone()
        assert result["canonical"] == '{"a":2,"z":1}'
    finally:
        conn.execute("reset role")


def test_service_role_reads_the_exact_bound_certified_artifact(conn):
    seeded = _seed(conn)
    rows = _lookup(conn, seeded["calibrator_id"])
    assert len(rows) == 1
    row = rows[0]
    assert row["calibration_artifact_id"] == seeded["calibrator_id"]
    assert row["certification_id"] == seeded["certification_id"]
    assert row["sport"] == "NBA"
    assert row["calibration_sha256"] == seeded["payload_sha"]
    assert row["artifact_payload"] == seeded["payload"]
    assert row["independent_verification_status"] == "PASS"


def test_legacy_certified_flag_alone_is_not_certification_authority(conn):
    seeded = _seed(conn, certify=False, legacy_status="CERTIFIED")
    assert _lookup(conn, seeded["calibrator_id"]) == []


def test_unknown_calibrator_uuid_returns_nothing(conn):
    _seed(conn)
    assert _lookup(conn, uuid.uuid4()) == []


def test_unsupported_sport_is_refused(conn):
    seeded = _seed(conn)
    assert _lookup(conn, seeded["calibrator_id"], sport="NFL") == []


def test_inactive_or_unpromoted_calibrator_is_invisible(conn):
    seeded = _seed(conn)
    conn.execute(
        "update public.wow_basketball_team_event_calibrators set active = false "
        "where calibrator_id = %s",
        (seeded["calibrator_id"],),
    )
    assert _lookup(conn, seeded["calibrator_id"]) == []


def test_active_and_promoted_are_restored_by_rollback_safe_flag_round_trip(conn):
    seeded = _seed(conn)
    conn.execute(
        "update public.wow_basketball_team_event_calibrators set promoted = false "
        "where calibrator_id = %s",
        (seeded["calibrator_id"],),
    )
    assert _lookup(conn, seeded["calibrator_id"]) == []
    conn.execute(
        "update public.wow_basketball_team_event_calibrators set promoted = true "
        "where calibrator_id = %s",
        (seeded["calibrator_id"],),
    )
    assert len(_lookup(conn, seeded["calibrator_id"])) == 1


# ---------------------------------------------------------------------------
# Certification authority failure modes
# ---------------------------------------------------------------------------


def test_shadow_review_required_payload_never_authorizes_even_with_registry_row(conn):
    payload = _payload(certification_status="REVIEW_REQUIRED")
    seeded = _seed(conn, payload=payload, league_scope="NBA_SHADOW_CASE")
    assert _lookup(conn, seeded["calibrator_id"]) == []


def test_revoked_certification_blocks_an_earlier_certified_row(conn):
    # Isolated league scope: revocation is a stateful fact and must not leak
    # into other cases through the registry's active-row selection.
    seeded = _seed(conn, revoked_after=True, league_scope="NBA_REVOCATION_CASE")
    assert _lookup(conn, seeded["calibrator_id"]) == []


def test_registry_digest_disagreement_with_calibrator_is_refused(conn):
    seeded = _seed(conn, certification_sha="d" * 64, league_scope="NBA_DIGEST_CASE")
    assert _lookup(conn, seeded["calibrator_id"]) == []


def test_certification_that_is_not_independently_verified_is_refused(conn):
    seeded = _seed(
        conn,
        independent_verification_status="FAIL",
        probability_publishable=False,
        league_scope="NBA_UNVERIFIED_CASE",
    )
    assert _lookup(conn, seeded["calibrator_id"]) == []


def test_certification_without_publication_authority_is_refused(conn):
    seeded = _seed(
        conn, probability_publishable=False, league_scope="NBA_NO_PUBLISH_CASE"
    )
    assert _lookup(conn, seeded["calibrator_id"]) == []


# ---------------------------------------------------------------------------
# Digest integrity
# ---------------------------------------------------------------------------


def test_python_and_postgresql_canonical_digests_agree_byte_for_byte(conn):
    payloads = [
        _payload(),
        _payload(calibration_bias=0.0, residual_quantile_90=0.5, training_n=1000),
        _payload(residual_basis="FULL_DATA_IN_SAMPLE_NOT_CERTIFIED_FOR_BOUNDS"),
        {"nested": {"z": [1, 2.5, None, True], "a": "é中"}, "platt_a": 1e-05},
    ]
    for payload in payloads:
        sql_digest = conn.execute(
            "select encode(sha256(convert_to("
            "public.wow_canonical_json_text(%s::jsonb), 'UTF8')), 'hex') as digest",
            (Jsonb(payload),),
        ).fetchone()["digest"]
        assert sql_digest == calibration_payload_sha256(payload)


def test_insert_with_a_digest_that_does_not_describe_the_payload_is_rejected(conn):
    with pytest.raises(psycopg.errors.RaiseException) as error:
        _seed(conn, digest="e" * 64)
    assert "BASKETBALL_CALIBRATION_ARTIFACT_DIGEST_MISMATCH" in str(error.value)


def test_payload_without_any_digest_is_rejected(conn):
    payload = _payload()
    with pytest.raises(psycopg.errors.RaiseException) as error:
        conn.execute(
            """
            insert into public.wow_basketball_team_event_calibrators (
              sport, calibrator_version, model_artifact_version, calibration_method,
              coefficients, calibration_row_count, calibration_artifact_payload,
              calibration_artifact_sha256
            ) values ('NBA', %s, %s, %s, '{}'::jsonb, 10, %s, null)
            """,
            (
                f"NBA_NO_DIGEST_{uuid.uuid4().hex[:8]}",
                f"NBA_NO_DIGEST_MODEL_{uuid.uuid4().hex[:8]}",
                METHOD,
                Jsonb(payload),
            ),
        )
    assert "BASKETBALL_CALIBRATION_ARTIFACT_DIGEST_MISSING" in str(error.value)


def test_tampered_payload_is_never_returned_even_with_the_declared_digest(conn):
    seeded = _seed(conn)
    assert len(_lookup(conn, seeded["calibrator_id"])) == 1
    # Simulate a privileged writer that bypasses the write-time guard: the
    # lookup must still recompute the digest and refuse the row.
    conn.execute(
        "alter table public.wow_basketball_team_event_calibrators "
        "disable trigger wow_basketball_calibrator_artifact_digest_guard"
    )
    try:
        tampered = {**seeded["payload"], "platt_a": 1.4, "residual_quantile_90": 0.31}
        conn.execute(
            "update public.wow_basketball_team_event_calibrators "
            "set calibration_artifact_payload = %s where calibrator_id = %s",
            (Jsonb(tampered), seeded["calibrator_id"]),
        )
    finally:
        conn.execute(
            "alter table public.wow_basketball_team_event_calibrators "
            "enable trigger wow_basketball_calibrator_artifact_digest_guard"
        )
    assert _lookup(conn, seeded["calibrator_id"]) == []


# ---------------------------------------------------------------------------
# Privileges, ownership, schema cache and migration order
# ---------------------------------------------------------------------------


def test_anon_and_authenticated_are_denied_execute(conn):
    for role in ("anon", "authenticated"):
        conn.execute(f"set role {role}")
        try:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(
                    "select * from public.wow_basketball_certified_calibrator_artifact"
                    "('NBA', %s::uuid)",
                    (uuid.uuid4(),),
                )
        finally:
            conn.execute("reset role")


def test_execute_privilege_is_service_role_only(conn):
    row = conn.execute(
        """
        select has_function_privilege('service_role', %s, 'EXECUTE') as service_role,
               has_function_privilege('anon', %s, 'EXECUTE') as anon,
               has_function_privilege('authenticated', %s, 'EXECUTE') as authenticated
        """,
        (RPC_SIGNATURE, RPC_SIGNATURE, RPC_SIGNATURE),
    ).fetchone()
    assert row == {"service_role": True, "anon": False, "authenticated": False}
    for helper in (
        "public.wow_canonical_json_text(jsonb)",
        "public.wow_basketball_calibrator_artifact_digest()",
    ):
        assert conn.execute(
            "select has_function_privilege('anon', %s, 'EXECUTE') as anon", (helper,)
        ).fetchone()["anon"] is False


def test_rpc_is_security_definer_with_empty_search_path(conn):
    row = conn.execute(
        """
        select p.prosecdef as security_definer,
               p.proconfig as config,
               pg_get_userbyid(p.proowner) as owner
          from pg_proc p
          join pg_namespace n on n.oid = p.pronamespace
         where n.nspname = 'public'
           and p.proname = 'wow_basketball_certified_calibrator_artifact'
        """
    ).fetchone()
    assert row["security_definer"] is True
    search_paths = [
        config for config in (row["config"] or []) if config.startswith("search_path=")
    ]
    assert search_paths, row["config"]
    assert all(
        config.split("=", 1)[1].strip('"') == "" for config in search_paths
    ), row["config"]
    assert row["owner"] not in {"anon", "authenticated", "service_role"}


def test_migration_requires_the_artifact_evidence_columns_first(conn, cluster):
    database = f"wow_order_{uuid.uuid4().hex[:10]}"
    conn.execute(f'create database "{database}"')
    try:
        from psycopg.conninfo import conninfo_to_dict

        isolated_params = conninfo_to_dict(cluster.dsn)
        isolated_params["dbname"] = database
        with psycopg.connect(
            **isolated_params, autocommit=True, row_factory=dict_row
        ) as isolated:
            _ensure_roles(isolated)
            for path in (IMMUTABILITY_SQL, BASKETBALL_SQL, CERTIFICATION_SQL):
                _apply(isolated, path)
            with pytest.raises(psycopg.errors.UndefinedColumn):
                _apply(isolated, RPC_SQL)
            _apply(isolated, EVIDENCE_SQL)
            _apply(isolated, RPC_SQL)
            # Re-applying the function is idempotent and refreshes the catalog.
            _apply(isolated, RPC_SQL)
            assert isolated.execute(
                "select count(*) as n from pg_proc p join pg_namespace n "
                "on n.oid = p.pronamespace where n.nspname = 'public' "
                "and p.proname = 'wow_basketball_certified_calibrator_artifact'"
            ).fetchone()["n"] == 1
    finally:
        conn.execute(f'drop database if exists "{database}"')
