"""Contract tests for the restored NBA/WNBA certified-calibrator lookup.

The V17 basketball pregame scorer has always called
`wow_basketball_certified_calibrator_artifact`, but no migration defined it, so
the call failed at PostgREST and surfaced as a generic scorer infrastructure
failure instead of a calibrator-artifact answer.

These tests pin the fail-closed database contract and the scorer's payload
unwrapping. They deliberately assert no certification, promotion, bounds
approval, or probability publication: the RPC is a lookup, not an authority.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from v17.multisport_team_event_calibration import (
    BINARY_ARTIFACT_TYPE,
    CalibrationArtifactInvalid,
    validate_binary_artifact,
)
from v17.nba_current_pregame_scorer import resolve_nba_calibrator_artifact

ENGINE_DIR = Path(__file__).resolve().parents[1]
MIGRATIONS = ENGINE_DIR / "migrations"
RPC_SQL = MIGRATIONS / "20261102_basketball_certified_calibrator_artifact_rpc.sql"
DEPENDENCY_SQL = (
    MIGRATIONS / "20260913_basketball_team_event_specialists.sql",
    MIGRATIONS / "20261007_basketball_calibration_artifact_evidence.sql",
)
SHA = "a" * 64
CAL_ID = "11111111-2222-3333-4444-555555555555"


def _sql() -> str:
    return RPC_SQL.read_text(encoding="utf-8")


def test_rpc_is_defined_with_the_exact_signature_the_scorer_calls():
    sql = _sql()
    assert (
        "create or replace function public.wow_basketball_certified_calibrator_artifact("
        in sql
    )
    assert "p_sport text" in sql
    assert "p_calibrator_id uuid" in sql
    for column in (
        "calibration_artifact_id",
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


def test_rpc_fails_closed_on_uncertified_shadow_or_unbound_calibrators():
    sql = _sql()
    assert "security definer" in sql
    assert "set search_path=''" in sql
    assert "cert.capability_status = 'CERTIFIED'" in sql
    assert "c.promoted" in sql
    assert "c.active" in sql
    # Exact version binding in both directions: a certification row for another
    # calibrator or another artifact can never authorize this payload.
    assert "cert.model_artifact_version = c.model_artifact_version" in sql
    assert "cert.calibrator_version = c.calibrator_version" in sql
    assert "c.calibration_artifact_payload is not null" in sql
    assert "c.calibration_artifact_sha256 ~ '^[0-9a-f]{64}$'" in sql


def test_every_selected_column_is_declared_by_a_dependency_migration():
    rpc = _sql()
    schema = "\n".join(path.read_text(encoding="utf-8") for path in DEPENDENCY_SQL)
    for column in (
        "calibrator_id",
        "sport",
        "model_artifact_version",
        "calibrator_version",
        "calibration_method",
        "calibration_artifact_sha256",
        "calibration_artifact_payload",
        "capability_status",
    ):
        assert column in rpc
        assert column in schema, f"{column} is not declared by a dependency migration"


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


def _payload(**overrides):
    payload = {
        "sport": "NBA",
        "artifact_type": BINARY_ARTIFACT_TYPE,
        "health_status": "PASS",
        "certification_status": "PASS",
        "calibration_method": "PLATT_TIME_SPLIT_V1",
        "calibration_version": "NBA_TEAM_EVENT_PLATT_V1_20261101T182242Z",
        "training_n": 240,
        "source_data_hash": SHA,
        "split_hash": "b" * 64,
        "fit_end": "2026-11-01T18:22:42Z",
        "brier_score": 0.24,
        "calibration_error": 0.03,
        "platt_a": 0.9,
        "platt_b": 0.1,
        "residual_quantile_90": 0.08,
    }
    payload.update(overrides)
    return payload


def _certification(sha: str = SHA):
    return SimpleNamespace(calibration_artifact_id=CAL_ID, calibration_sha256=sha)


def test_payload_row_is_unwrapped_with_database_identity_authoritative():
    row = {
        "calibration_artifact_id": CAL_ID,
        "sport": "NBA",
        "calibration_sha256": SHA,
        "artifact_payload": _payload(
            calibration_sha256="f" * 64,
            calibration_artifact_id="spoofed",
        ),
    }
    client = _Client(row)
    resolved = resolve_nba_calibrator_artifact(client, _certification())
    # Bound columns win over anything the payload claims about itself.
    assert resolved["calibration_artifact_id"] == CAL_ID
    assert resolved["calibration_sha256"] == SHA
    assert resolved["sport"] == "NBA"
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


def test_bound_fingerprint_mismatch_fails_closed():
    row = {
        "calibration_artifact_id": CAL_ID,
        "sport": "NBA",
        "calibration_sha256": "c" * 64,
        "artifact_payload": _payload(),
    }
    with pytest.raises(ValueError, match="NBA_CERTIFIED_CALIBRATOR_CERTIFICATION_MISMATCH"):
        resolve_nba_calibrator_artifact(_Client(row), _certification())


def test_payload_without_bound_fingerprint_cannot_pass_the_lookup():
    row = {
        "calibration_artifact_id": CAL_ID,
        "sport": "NBA",
        "calibration_sha256": None,
        "artifact_payload": _payload(),
    }
    with pytest.raises(ValueError, match="NBA_CERTIFIED_CALIBRATOR_CERTIFICATION_MISMATCH"):
        resolve_nba_calibrator_artifact(_Client(row), _certification())


def test_shadow_payload_cannot_become_publishable_through_the_lookup():
    row = {
        "calibration_artifact_id": CAL_ID,
        "sport": "NBA",
        "calibration_sha256": SHA,
        "artifact_payload": _payload(certification_status="REVIEW_REQUIRED"),
    }
    resolved = resolve_nba_calibrator_artifact(_Client(row), _certification())
    with pytest.raises(CalibrationArtifactInvalid) as error:
        validate_binary_artifact(resolved, "NBA")
    assert "CALIBRATION_CERTIFICATION_NOT_PASS" in str(error.value)


def test_transport_failure_is_never_reported_as_a_missing_artifact():
    with pytest.raises(RuntimeError, match="NBA_CALIBRATOR_RESOLUTION_FAILED"):
        resolve_nba_calibrator_artifact(
            _Client(None, failure=RuntimeError("transport")),
            _certification(),
        )
