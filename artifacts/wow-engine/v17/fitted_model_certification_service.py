"""Durable audit boundary for V17 fitted-model certification and promotion.

The pure contract lives in ``v17.fitted_model_certification_promotion``.  This
module adds the service-role receipt store and authenticated internal routes.
It still cannot promote, publish, rank, or execute: it records deterministic
replay evidence and governed-apply packets for independent review.
"""
from __future__ import annotations

import os
from typing import Any, Mapping

from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field

from github_actions_oidc import scout_route_auth_dependency
from v17.fitted_model_certification_promotion import (
    APPROVAL_KEY_ENV,
    CAN_EXECUTE,
    DEFAULT_REQUIRED_METRICS,
    PROMOTION_APPROVED,
    PROMOTION_BLOCKED,
    PROMOTION_READY,
    FittedCertificationError,
    add_promotion_blockers,
    build_cross_sport_matrix,
    evaluate_fitted_model_promotion,
    parse_promotion_authorization,
    parse_promotion_policy,
    replay_fitted_artifact,
    verify_authorization_signature,
    verify_policy_signature,
)

REPLAY_TABLE = "wow_fitted_model_certification_replay_receipts"
PROMOTION_TABLE = "wow_fitted_model_promotion_receipts"
REPLAY_CONFLICT_COLUMNS = (
    "artifact_id,artifact_checksum,received_corpus_sha256,verifier_version,receipt_sha256"
)
PROMOTION_CONFLICT_COLUMNS = (
    "artifact_id,artifact_checksum,policy_sha256,authorization_sha256,promotion_sha256"
)
REPLAY_OPERATION_ID = "runWowV17FittedModelCertificationReplay"
PROMOTION_OPERATION_ID = "runWowV17FittedModelPromotionDecision"
MATRIX_OPERATION_ID = "runWowV17FittedModelCertificationMatrix"


class FittedReplayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifact: dict[str, Any]
    required_metrics: list[str] = Field(
        default_factory=lambda: list(DEFAULT_REQUIRED_METRICS)
    )


class FittedPromotionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifact: dict[str, Any]
    policy: dict[str, Any] | None = None
    authorization: dict[str, Any] | None = None
    required_metrics: list[str] = Field(
        default_factory=lambda: list(DEFAULT_REQUIRED_METRICS)
    )


class FittedMatrixRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    policies: list[dict[str, Any]] = Field(default_factory=list)
    authorizations: list[dict[str, Any]] = Field(default_factory=list)
    required_metrics: list[str] = Field(
        default_factory=lambda: list(DEFAULT_REQUIRED_METRICS)
    )


def _rows(result: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in (getattr(result, "data", None) or [])]


def _find_one(db: Any, table: str, filters: Mapping[str, Any]) -> dict[str, Any] | None:
    query = db.table(table).select("*")
    for key, value in filters.items():
        query = query.eq(key, value)
    rows = _rows(query.limit(1).execute())
    return rows[0] if rows else None


def _insert_idempotent(
    db: Any,
    table: str,
    record: Mapping[str, Any],
    filters: Mapping[str, Any],
    conflict_columns: str,
) -> tuple[dict[str, Any], bool]:
    """Insert once and resolve a concurrent duplicate to the existing row."""
    query = db.table(table)
    rows: list[dict[str, Any]] = []
    insert_error: Exception | None = None
    if hasattr(query, "upsert"):
        rows = _rows(
            query.upsert(
                dict(record),
                on_conflict=conflict_columns,
                ignore_duplicates=True,
            ).execute()
        )
    else:
        try:
            rows = _rows(query.insert(dict(record)).execute())
        except Exception as exc:  # duplicate race or real store failure
            insert_error = exc
    if rows:
        return rows[0], False
    existing = _find_one(db, table, filters)
    if existing is not None:
        return existing, True
    if insert_error is not None:
        raise insert_error
    raise RuntimeError("RECEIPT_INSERT_NOT_VISIBLE")


def _receipt_id(row: Mapping[str, Any]) -> str | None:
    value = row.get("receipt_id") or row.get("id")
    return str(value) if value else None


def _identity_fields(receipt: Mapping[str, Any]) -> dict[str, Any]:
    identity = receipt.get("identity")
    if not isinstance(identity, Mapping):
        identity = receipt.get("artifact_identity")
    if not isinstance(identity, Mapping):
        identity = {}
    return {
        "artifact_id": str(identity.get("artifact_id") or "__UNIDENTIFIED__"),
        "sport": str(identity.get("sport") or "__UNIDENTIFIED__"),
        "league": str(identity.get("league") or "__UNIDENTIFIED__"),
        "surface": str(identity.get("surface") or "__UNIDENTIFIED__"),
        "model_family": str(identity.get("model_family") or "__UNIDENTIFIED__"),
        "stat_type": identity.get("stat_type"),
        "model_artifact_version": str(identity.get("model_artifact_version") or "__UNIDENTIFIED__"),
        "artifact_checksum": str(identity.get("artifact_checksum") or "__UNIDENTIFIED__"),
    }


def _replay_record(receipt: Mapping[str, Any]) -> dict[str, Any]:
    identity = _identity_fields(receipt)
    return {
        **identity,
        "status": str(receipt.get("status") or "CERTIFICATION_REPLAY_FAILED"),
        "verifier_version": str(receipt.get("verifier_version") or "UNKNOWN"),
        "blockers": list(receipt.get("blockers") or []),
        "metrics": dict(receipt.get("metrics") or {}),
        "partition_counts": dict(receipt.get("partition_counts") or {}),
        "settlement_counts": dict(receipt.get("settlement_counts") or {}),
        "partition_settled_counts": dict(receipt.get("partition_settled_counts") or {}),
        # Keep this aligned with _replay_filters: an unidentifiable packet must
        # still be idempotent rather than inserting NULL-keyed duplicates.
        "received_corpus_sha256": receipt.get("received_corpus_sha256") or "__UNIDENTIFIED__",
        "receipt_sha256": receipt.get("receipt_sha256"),
        "payload": dict(receipt),
        "probability_publishable": False,
        "can_execute": False,
    }


def _replay_filters(receipt: Mapping[str, Any]) -> dict[str, Any]:
    identity = _identity_fields(receipt)
    return {
        "artifact_id": identity["artifact_id"],
        "artifact_checksum": identity["artifact_checksum"],
        "received_corpus_sha256": receipt.get("received_corpus_sha256") or "__UNIDENTIFIED__",
        "verifier_version": str(receipt.get("verifier_version") or "UNKNOWN"),
        "receipt_sha256": str(receipt.get("receipt_sha256") or "__UNIDENTIFIED__"),
    }


def persist_replay_receipt(db: Any, receipt: Mapping[str, Any]) -> dict[str, Any]:
    """Persist one replay receipt idempotently; never claim audit completion on failure."""
    if db is None:
        return {
            "receipt_id": None,
            "receipt_persisted": False,
            "receipt_idempotent": False,
            "audit_complete": False,
            "receipt_store_error": "DB_CLIENT_UNAVAILABLE",
        }
    filters = _replay_filters(receipt)
    try:
        stored, idempotent = _insert_idempotent(
            db,
            REPLAY_TABLE,
            _replay_record(receipt),
            filters,
            REPLAY_CONFLICT_COLUMNS,
        )
        return {
            "receipt_id": _receipt_id(stored),
            "receipt_persisted": True,
            "receipt_idempotent": idempotent,
            "audit_complete": True,
            "receipt_store_error": None,
        }
    except Exception as exc:  # noqa: BLE001 - audit persistence must fail closed
        return {
            "receipt_id": None,
            "receipt_persisted": False,
            "receipt_idempotent": False,
            "audit_complete": False,
            "receipt_store_error": type(exc).__name__,
        }


def _promotion_record(packet: Mapping[str, Any]) -> dict[str, Any]:
    identity = _identity_fields(packet)
    replay = packet.get("replay") or {}
    policy = packet.get("policy") or {}
    authorization = packet.get("authorization") or {}
    return {
        **identity,
        "status": str(packet.get("status") or PROMOTION_BLOCKED),
        "policy_id": str(policy.get("policy_id") or "__NO_POLICY__"),
        "policy_sha256": str(packet.get("policy_sha256") or "__NO_POLICY__"),
        "authorization_id": str(authorization.get("authorization_id") or "__NO_AUTHORIZATION__"),
        "authorization_sha256": str(packet.get("authorization_sha256") or "__NO_AUTHORIZATION__"),
        "replay_receipt_sha256": str(replay.get("receipt_sha256") or "__UNIDENTIFIED__"),
        "blockers": list(packet.get("blockers") or []),
        "promotion_sha256": str(packet.get("promotion_sha256") or "__UNIDENTIFIED__"),
        "apply_packet": packet.get("apply_packet"),
        "payload": dict(packet),
        "probability_publishable": False,
        "can_execute": False,
    }


def _promotion_filters(packet: Mapping[str, Any]) -> dict[str, Any]:
    record = _promotion_record(packet)
    return {
        "artifact_id": record["artifact_id"],
        "artifact_checksum": record["artifact_checksum"],
        "policy_sha256": record["policy_sha256"],
        "authorization_sha256": record["authorization_sha256"],
        "promotion_sha256": record["promotion_sha256"],
    }


def persist_promotion_receipt(db: Any, packet: Mapping[str, Any]) -> dict[str, Any]:
    """Persist one promotion decision idempotently; never implies registry mutation."""
    if db is None:
        return {
            "promotion_receipt_id": None,
            "promotion_receipt_persisted": False,
            "promotion_receipt_idempotent": False,
            "audit_complete": False,
            "promotion_store_error": "DB_CLIENT_UNAVAILABLE",
        }
    filters = _promotion_filters(packet)
    try:
        stored, idempotent = _insert_idempotent(
            db,
            PROMOTION_TABLE,
            _promotion_record(packet),
            filters,
            PROMOTION_CONFLICT_COLUMNS,
        )
        return {
            "promotion_receipt_id": _receipt_id(stored),
            "promotion_receipt_persisted": True,
            "promotion_receipt_idempotent": idempotent,
            "audit_complete": True,
            "promotion_store_error": None,
        }
    except Exception as exc:  # noqa: BLE001 - audit persistence must fail closed
        return {
            "promotion_receipt_id": None,
            "promotion_receipt_persisted": False,
            "promotion_receipt_idempotent": False,
            "audit_complete": False,
            "promotion_store_error": type(exc).__name__,
        }


def _contract_failure(code: str, detail: str) -> dict[str, Any]:
    return {
        "status": "FITTED_MODEL_CERTIFICATION_CONTRACT_FAILED",
        "code": code,
        "blockers": [detail],
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "can_execute": False,
    }


def _runtime_failure(exc: Exception) -> dict[str, Any]:
    return {
        "status": "FITTED_MODEL_CERTIFICATION_RUNTIME_FAILED",
        "code": "FITTED_MODEL_CERTIFICATION_RUNTIME_FAILED",
        "error_type": type(exc).__name__,
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "can_execute": False,
    }


def run_fitted_model_replay(request: FittedReplayRequest, *, db: Any) -> dict[str, Any]:
    receipt = replay_fitted_artifact(
        request.artifact,
        required_metrics=tuple(request.required_metrics),
    )
    return {**receipt, **persist_replay_receipt(db, receipt)}


def run_fitted_model_promotion(
    request: FittedPromotionRequest,
    *,
    db: Any,
    approval_key: str | None = None,
) -> dict[str, Any]:
    """Evaluate a promotion only with governance-signed policy and approval."""
    try:
        policy = parse_promotion_policy(request.policy) if request.policy else None
    except FittedCertificationError as exc:
        return _contract_failure("PROMOTION_POLICY_INVALID", exc.code)
    try:
        authorization = (
            parse_promotion_authorization(request.authorization)
            if request.authorization
            else None
        )
    except FittedCertificationError as exc:
        return _contract_failure("PROMOTION_AUTHORIZATION_INVALID", exc.code)

    trust_key = approval_key if approval_key is not None else os.environ.get(APPROVAL_KEY_ENV)
    try:
        if policy is not None:
            verify_policy_signature(policy, trust_key)
        if authorization is not None:
            verify_authorization_signature(authorization, trust_key)
    except FittedCertificationError as exc:
        return _contract_failure("PROMOTION_TRUST_ROOT_INVALID", exc.code)

    # Persist and evaluate the same mandatory metric set.  A caller may request
    # additional diagnostics, but cannot narrow the replay used for promotion.
    required_metrics = tuple(
        dict.fromkeys((*DEFAULT_REQUIRED_METRICS, *request.required_metrics))
    )
    replay = replay_fitted_artifact(request.artifact, required_metrics=required_metrics)
    replay_persisted = persist_replay_receipt(db, replay)
    packet = evaluate_fitted_model_promotion(
        request.artifact,
        policy=policy,
        authorization=authorization,
        required_metrics=required_metrics,
    )

    # A promotion decision is not auditable unless its exact replay receipt is
    # durable. Downgrade before writing the promotion record so the stored
    # packet cannot disagree with the response.
    if (
        not replay_persisted["receipt_persisted"]
        and packet["status"] in {PROMOTION_READY, PROMOTION_APPROVED}
    ):
        packet = add_promotion_blockers(
            packet,
            ("PROMOTION_REPLAY_RECEIPT_NOT_PERSISTED",),
            status=PROMOTION_BLOCKED,
        )

    promotion_persisted = persist_promotion_receipt(db, packet)
    if not promotion_persisted["promotion_receipt_persisted"]:
        packet = add_promotion_blockers(
            packet,
            ("PROMOTION_RECEIPT_NOT_PERSISTED",),
            status=(
                PROMOTION_BLOCKED
                if packet["status"] == PROMOTION_APPROVED
                else str(packet["status"])
            ),
        )

    audit_complete = bool(
        replay_persisted["receipt_persisted"]
        and promotion_persisted["promotion_receipt_persisted"]
    )
    return {
        **packet,
        **promotion_persisted,
        "replay_receipt_id": replay_persisted["receipt_id"],
        "replay_receipt_persisted": replay_persisted["receipt_persisted"],
        "replay_receipt_idempotent": replay_persisted["receipt_idempotent"],
        "audit_complete": audit_complete,
    }


def run_fitted_model_matrix(request: FittedMatrixRequest) -> dict[str, Any]:
    try:
        policies = [parse_promotion_policy(payload) for payload in request.policies]
        authorizations = [
            parse_promotion_authorization(payload)
            for payload in request.authorizations
        ]
    except FittedCertificationError as exc:
        return _contract_failure("FITTED_MODEL_CERTIFICATION_MATRIX_INVALID", exc.code)
    return build_cross_sport_matrix(
        request.artifacts,
        policies=policies,
        authorizations=authorizations,
        required_metrics=tuple(request.required_metrics),
    )


def install_fitted_model_certification_promotion_routes(
    app: FastAPI,
    *,
    auth_dependency: Any,
    db_client_fn: Any,
) -> None:
    """Install authenticated internal routes exactly once per path."""
    replay_path = "/internal/v17/fitted-model-certification/replay"
    promotion_path = "/internal/v17/fitted-model-certification/promotion"
    matrix_path = "/internal/v17/fitted-model-certification/matrix"
    existing = {getattr(route, "path", None) for route in app.router.routes}

    if replay_path not in existing:
        @app.post(
            replay_path,
            dependencies=[scout_route_auth_dependency(auth_dependency)],
            operation_id=REPLAY_OPERATION_ID,
        )
        def replay_route(request: FittedReplayRequest) -> dict[str, Any]:
            try:
                return run_fitted_model_replay(request, db=db_client_fn())
            except Exception as exc:  # noqa: BLE001 - typed, non-MODEL_UNAVAILABLE failure
                return _runtime_failure(exc)

    if promotion_path not in existing:
        @app.post(
            promotion_path,
            dependencies=[scout_route_auth_dependency(auth_dependency)],
            operation_id=PROMOTION_OPERATION_ID,
        )
        def promotion_route(request: FittedPromotionRequest) -> dict[str, Any]:
            try:
                return run_fitted_model_promotion(request, db=db_client_fn())
            except Exception as exc:  # noqa: BLE001 - typed, non-MODEL_UNAVAILABLE failure
                return _runtime_failure(exc)

    if matrix_path not in existing:
        @app.post(
            matrix_path,
            dependencies=[scout_route_auth_dependency(auth_dependency)],
            operation_id=MATRIX_OPERATION_ID,
        )
        def matrix_route(request: FittedMatrixRequest) -> dict[str, Any]:
            try:
                return run_fitted_model_matrix(request)
            except Exception as exc:  # noqa: BLE001 - typed, non-MODEL_UNAVAILABLE failure
                return _runtime_failure(exc)


__all__ = [
    "CAN_EXECUTE",
    "FittedMatrixRequest",
    "FittedPromotionRequest",
    "FittedReplayRequest",
    "MATRIX_OPERATION_ID",
    "PROMOTION_OPERATION_ID",
    "PROMOTION_TABLE",
    "REPLAY_OPERATION_ID",
    "REPLAY_TABLE",
    "install_fitted_model_certification_promotion_routes",
    "persist_promotion_receipt",
    "persist_replay_receipt",
    "run_fitted_model_matrix",
    "run_fitted_model_promotion",
    "run_fitted_model_replay",
]
