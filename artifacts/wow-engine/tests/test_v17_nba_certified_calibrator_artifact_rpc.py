"""Contract tests for the restored NBA/WNBA certified-calibrator lookup.

The V17 basketball pregame scorer has always called
`wow_basketball_certified_calibrator_artifact`, but no migration defined it, so
the call failed at PostgREST and surfaced as a generic scorer infrastructure
failure instead of a calibrator-artifact answer.

These tests pin two independent contracts:

* the database lookup is authorized ONLY by an immutable, independently
  verified V17 certification row (the legacy basketball `capability_status`
  flag is not authority), and
* the returned payload digest is recomputed from payload content, so a mutated
  coefficient with an unchanged declared fingerprint cannot pass.

They deliberately assert no certification, promotion, bounds approval, or
probability publication: the RPC is a lookup, not an authority. The
database-backed proof (grants, revocation, shadow artifacts, tampering in real
PostgreSQL) lives in test_v17_basketball_calibrator_rpc_postgres.py.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from v17.multisport_team_event_calibration import (
    BINARY_ARTIFACT_TYPE,
    CalibrationArtifactInvalid,
    calibration_payload_sha256,
    validate_binary_artifact,
)
from v17.nba_current_pregame_scorer import resolve_nba_calibrator_artifact

ENGINE_DIR = Path(__file__).resolve().parents[1]
MIGRATIONS = ENGINE_DIR / "migrations"
RPC_SQL = MIGRATIONS / "20261102_basketball_certified_calibrator_artifact_rpc.sql"
DEPENDENCY_SQL = (
    MIGRATIONS / "20260913_basketball_team_event_specialists.sql",
    MIGRATIONS / "20260921_create_team_event_specialist_certifications.sql",
    MIGRATIONS / "20261007_basketball_calibration_artifact_evidence.sql",
)
SHA = "a" * 64
CAL_ID = "11111111-2222-3333-4444-555555555555"
CERT_ID = "99999999-8888-7777-6666-555555555555"
METHOD = "PLATT_TIME_SPLIT_V1"
CALIBRATOR_VERSION = "NBA_TEAM_EVENT_PLATT_V1_20261101T182242Z"


def _sql() -> str:
    return RPC_SQL.read_text(encoding="utf-8")


def _code_sql() -> str:
    return "\n".join(
        line for line in _sql().splitlines() if not line.lstrip().startswith("--")
    )


def _payload(**overrides):
    payload = {
        "sport": "NBA",
        "artifact_type": BINARY_ARTIFACT_TYPE,
        "health_status": "PASS",
        "certification_status": "PASS",
        "calibration_method": METHOD,
        "calibration_version": CALIBRATOR_VERSION,
        "training_n": 240,
        "source_data_hash": SHA,
        "split_hash": "b" * 64,
        "fit_end": "2026-11-01T18:22:42Z",
        "brier_score": 0.24,
        "calibration_error": 0.03,
        "platt_a": 0.9,
        "platt_b": 0.1,
        "residual_quantile_90": 0.08,
        "can_execute": False,
        "probability_publishable": False,
    }
    payload.update(overrides)
    return payload


def _paid(**overrides):
    payload = _payload(**overrides)
    return payload, calibration_payload_sha256(payload)


def _certification(sha: str = SHA, **overrides):
    values = {
        "calibration_artifact_id": CAL_ID,
        "calibration_sha256": sha,
        "certification_id": CERT_ID,
        "calibration_method": METHOD,
        "independent_verification_status": "PASS",
        "certification_status": "CERTIFIED",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _lookup_row(payload, sha, **overrides):
    row = {
        "calibration_artifact_id": CAL_ID,
        "certification_id": CERT_ID,
        "sport": "NBA",
        "model_artifact_version": "NBA_TEAM_EVENT_LOGISTIC_V1_20261101T182242Z",
        "calibrator_version": CALIBRATOR_VERSION,
        "calibration_method": METHOD,
        "calibration_sha256": sha,
        "independent_verification_status": "PASS",
        "artifact_payload": payload,
        "bounds_approval_receipt": None,
    }
    row.update(overrides)
    return row


class _Response:
    def __init__(self, data: object, failure: Exception | None = None) -> None:
        self.data = data
        self._failure = failure

    def execute(self):
        if self._failure is not None:
            raise self._failure
        return self


class _Client:
    def __init__(self, data: object, failure: Exception | None = None) -> None:
        self.data = data
        self.failure = failure
        self.calls: list[tuple[str, dict[str, object]]] = []

    def rpc(self, name: str, params: dict[str, object]):
        self.calls.append((name, dict(params)))
        return _Response(self.data, self.failure)

    def table(self, _name: str):
        raise AssertionError("direct-table fallback is forbidden")


# ---------------------------------------------------------------------------
# SQL contract
# ---------------------------------------------------------------------------


def test_rpc_is_defined_with_the_exact_signature_the_scorer_calls():
    sql = _sql()
    assert (
        "create function public.wow_basketball_certified_calibrator_artifact("
        in sql
    )
    assert "p_sport text" in sql
    assert "p_calibrator_id uuid" in sql
    for column in (
        "calibration_artifact_id",
        "certification_id",
        "sport",
        "calibration_sha256",
        "artifact_payload",
    ):
        assert column in sql


def test_rpc_is_service_role_only():
    sql = _sql()
    for role in ("public", "anon", "authenticated"):
        assert (
            "revoke all on function "
            f"public.wow_basketball_certified_calibrator_artifact(text, uuid) from {role};"
        ) in sql
    assert (
        "grant execute on function "
        "public.wow_basketball_certified_calibrator_artifact(text, uuid) to service_role;"
    ) in sql
    assert "to anon" not in sql
    assert "to authenticated" not in sql


def test_rpc_authority_is_the_independent_v17_certification_registry():
    sql = _sql()
    code = _code_sql()
    assert "security definer" in sql
    assert "set search_path=''" in sql
    assert "public.wow_team_event_specialist_certifications" in code
    assert "cert.certification_status = 'CERTIFIED'" in code
    assert "cert.independent_verification_status = 'PASS'" in code
    assert "cert.probability_publishable is true" in code
    # The legacy basketball capability flag is explicitly not authority: it is
    # named in the migration comment and nowhere in the executed SQL.
    assert "wow_basketball_team_event_certification" not in code
    assert "capability_status" not in code


def test_rpc_binds_exact_immutable_calibrator_identity_and_digest():
    sql = _sql()
    assert "cert.calibration_artifact_id = c.calibrator_id::text" in sql
    assert "cert.calibration_sha256 = c.calibration_artifact_sha256" in sql
    assert "cert.calibration_method = c.calibration_method" in sql
    assert "cert.model_version = c.model_artifact_version" in sql
    assert "cert.certification_id = (" in sql


def test_rpc_requires_revocation_evidence_to_be_absent():
    sql = _sql()
    assert "revoked.certification_status = 'REVOKED'" in sql
    assert "not exists (" in sql


def test_rpc_recomputes_the_payload_digest_server_side():
    code = _code_sql()
    assert "public.wow_canonical_json_text(c.calibration_artifact_payload)" in code
    assert "sha256(convert_to(" in code
    assert ") = c.calibration_artifact_sha256" in code
    # The recomputed digest is the guard; a regex on the declared column alone
    # would not catch a mutated payload and is never the only check.
    digest_guard = code.split("sha256(convert_to(", 1)[1]
    assert "= c.calibration_artifact_sha256" in digest_guard


def test_rpc_refuses_shadow_artifacts_that_self_describe_as_unreviewed():
    sql = _sql()
    assert "->>'certification_status' = 'PASS'" in sql
    assert "->>'health_status' = 'PASS'" in sql
    assert "->>'artifact_type' = 'BINARY_PLATT_CALIBRATOR'" in sql
    assert "->>'calibration_version' = c.calibrator_version" in sql


def test_dependency_migrations_declare_digest_guard_and_canonical_serializer():
    sql = _sql()
    dependency = "\n".join(path.read_text(encoding="utf-8") for path in DEPENDENCY_SQL)
    assert "create or replace function public.wow_canonical_json_text" in dependency
    assert "create trigger wow_basketball_calibrator_artifact_digest_guard" in dependency
    assert "BASKETBALL_CALIBRATION_ARTIFACT_DIGEST_MISMATCH" in dependency
    for column in (
        "calibration_artifact_payload",
        "calibration_artifact_sha256",
    ):
        assert column in sql
        assert column in dependency


# ---------------------------------------------------------------------------
# Scorer payload verification
# ---------------------------------------------------------------------------


def test_verified_payload_is_unwrapped_with_database_identity_authoritative():
    payload, sha = _paid(calibration_artifact_id="spoofed", calibration_sha256="f" * 64)
    client = _Client(_lookup_row(payload, sha))
    resolved = resolve_nba_calibrator_artifact(client, _certification(sha))
    assert resolved["calibration_artifact_id"] == CAL_ID
    assert resolved["calibration_sha256"] == sha
    assert resolved["sport"] == "NBA"
    assert resolved["certification_id"] == CERT_ID
    assert resolved["training_n"] == 240
    assert client.calls == [
        (
            "wow_basketball_certified_calibrator_artifact",
            {"p_sport": "NBA", "p_calibrator_id": CAL_ID},
        )
    ]


def test_flat_artifact_row_still_passes_through_unchanged():
    artifact = {**_payload(), "calibration_artifact_id": CAL_ID, "calibration_sha256": SHA}
    assert resolve_nba_calibrator_artifact(_Client(artifact), _certification()) == artifact


@pytest.mark.parametrize(
    "mutation",
    [
        {"platt_a": 1.4},
        {"platt_b": -0.3},
        {"residual_quantile_90": 0.31},
        {"bounds_policy": "APPROVED"},
        {"probability_publishable": True},
        {"training_n": 12},
    ],
)
def test_tampered_payload_with_unchanged_declared_digest_fails_closed(mutation):
    payload, sha = _paid()
    payload.update(mutation)
    client = _Client(_lookup_row(payload, sha))
    with pytest.raises(ValueError, match="CALIBRATION_ARTIFACT_PAYLOAD_DIGEST_MISMATCH"):
        resolve_nba_calibrator_artifact(client, _certification(sha))


def test_restamped_declared_digest_cannot_outrun_the_certified_digest():
    payload, _ = _paid()
    payload["platt_a"] = 1.4
    forged = calibration_payload_sha256(payload)
    client = _Client(_lookup_row(payload, forged))
    with pytest.raises(ValueError, match="NBA_CERTIFIED_CALIBRATOR_CERTIFICATION_MISMATCH"):
        resolve_nba_calibrator_artifact(client, _certification(SHA))


def test_bound_fingerprint_mismatch_fails_closed():
    payload, sha = _paid()
    row = _lookup_row(payload, "c" * 64)
    with pytest.raises(ValueError, match="NBA_CERTIFIED_CALIBRATOR_CERTIFICATION_MISMATCH"):
        resolve_nba_calibrator_artifact(_Client(row), _certification(sha))


def test_payload_without_bound_fingerprint_cannot_pass_the_lookup():
    payload, sha = _paid()
    row = _lookup_row(payload, None)
    with pytest.raises(ValueError, match="NBA_CERTIFIED_CALIBRATOR_CERTIFICATION_MISMATCH"):
        resolve_nba_calibrator_artifact(_Client(row), _certification(sha))


def test_digest_with_invalid_hex_shape_cannot_pass_the_lookup():
    payload, _ = _paid()
    row = _lookup_row(payload, "z" * 64)
    with pytest.raises(ValueError, match="NBA_CERTIFIED_CALIBRATOR_CERTIFICATION_MISMATCH"):
        resolve_nba_calibrator_artifact(_Client(row), _certification("z" * 64))


def test_row_bound_to_another_calibrator_uuid_is_rejected():
    payload, sha = _paid()
    row = _lookup_row(payload, sha, calibration_artifact_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    with pytest.raises(ValueError, match="NBA_CERTIFIED_CALIBRATOR_CERTIFICATION_MISMATCH"):
        resolve_nba_calibrator_artifact(_Client(row), _certification(sha))


def test_row_bound_to_another_certification_receipt_is_rejected():
    payload, sha = _paid()
    row = _lookup_row(payload, sha, certification_id="00000000-0000-0000-0000-000000000000")
    with pytest.raises(ValueError, match="NBA_CERTIFIED_CALIBRATOR_CERTIFICATION_MISMATCH"):
        resolve_nba_calibrator_artifact(_Client(row), _certification(sha))


def test_row_with_another_calibration_method_is_rejected():
    payload, sha = _paid()
    row = _lookup_row(payload, sha, calibration_method="ISOTONIC_V1")
    with pytest.raises(ValueError, match="NBA_CERTIFIED_CALIBRATOR_METHOD_MISMATCH"):
        resolve_nba_calibrator_artifact(_Client(row), _certification(sha))


def test_shadow_payload_cannot_become_publishable_through_the_lookup():
    payload, sha = _paid(certification_status="REVIEW_REQUIRED")
    resolved = resolve_nba_calibrator_artifact(
        _Client(_lookup_row(payload, sha)), _certification(sha)
    )
    with pytest.raises(CalibrationArtifactInvalid) as error:
        validate_binary_artifact(resolved, "NBA")
    assert "CALIBRATION_CERTIFICATION_NOT_PASS" in str(error.value)


@pytest.mark.parametrize("missing_rows", [None, []])
def test_missing_calibrator_rpc_rows_are_unavailable_not_invalid(missing_rows):
    """PostgREST returns [] for a valid no-match, not a broken artifact."""
    client = _Client(missing_rows)
    with pytest.raises(LookupError, match="^NBA_CERTIFIED_CALIBRATOR_UNAVAILABLE$"):
        resolve_nba_calibrator_artifact(client, _certification())
    assert client.calls == [
        ("wow_basketball_certified_calibrator_artifact",
         {"p_sport": "NBA", "p_calibrator_id": CAL_ID}),
    ]


def test_transport_failure_is_never_reported_as_a_missing_artifact():
    with pytest.raises(RuntimeError, match="NBA_CALIBRATOR_RESOLUTION_FAILED"):
        resolve_nba_calibrator_artifact(
            _Client(None, failure=RuntimeError("transport")),
            _certification(),
        )


def test_bounds_approval_is_an_independent_registry_receipt_not_content_self_hash():
    code = _code_sql()
    assert "bounds_approval_receipt jsonb" in code
    assert "cert.verification_receipt->'nba_bounds_approval'" in code
    assert "c.calibration_artifact_payload->'nba_bounds_approval'" not in code
    payload, sha = _paid(
        bounds_approval_receipt={
            "status": "APPROVED", "calibration_sha256": "f" * 64,
        }
    )
    approved = {
        "status": "APPROVED",
        "policy_version": "NBA_DYNAMIC_BOUNDS_V1",
        "independent_verification_status": "PASS",
        "calibration_artifact_id": CAL_ID,
        "calibration_sha256": sha,
        "certification_id": CERT_ID,
        "can_execute": False,
    }
    resolved = resolve_nba_calibrator_artifact(
        _Client(_lookup_row(payload, sha, bounds_approval_receipt=approved)),
        _certification(sha),
    )
    assert resolved["bounds_approval_receipt"] == approved
    assert resolved["bounds_approval_receipt"] != payload["bounds_approval_receipt"]
    assert resolved["calibration_sha256"] == sha


def test_payload_cannot_inject_bounds_approval_when_registry_receipt_is_missing():
    payload, sha = _paid(
        bounds_approval_receipt={
            "status": "APPROVED",
            "calibration_sha256": "f" * 64,
            "certification_id": CERT_ID,
        }
    )
    resolved = resolve_nba_calibrator_artifact(
        _Client(_lookup_row(payload, sha, bounds_approval_receipt=None)),
        _certification(sha),
    )
    assert resolved["bounds_approval_receipt"] is None
