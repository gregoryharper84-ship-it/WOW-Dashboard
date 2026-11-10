"""Wire the NBA independent verification harness into governed maintenance.

Every successful NBA fit now produces a verification receipt and archives it in
an append-only ledger before the maintenance run may report success. A receipt
that does not pass fails the run.

Scope and limits:

- verification reads persisted rows only; it never re-fits, promotes, certifies,
  publishes a probability, or touches `can_execute`;
- the ledger row is evidence, not a certification decision. Certification stays
  a separate governed act that requires an independently reviewed PASS receipt;
- a run fails closed when verification cannot be constructed, when the receipt
  cannot be archived, or when the receipt reports a blocker the caller did not
  explicitly defer.

Deferral exists because two of the twelve checks attest governance states that a
nightly fitting run cannot legitimately self-issue: V10 asserts the artifact is
already CERTIFIED, and V11 asserts an independent source-event replay
attestation. A freshly fitted artifact is persisted SHADOW by design, and the
maintenance lane holds no independent attestation. Those two blockers may be
deferred *explicitly and by code*; any other failure - reproducibility,
provenance, freshness, pagination, calibration, fit gates, substitution - always
fails the run, and no deferral can hide a version mismatch.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from v17.nba_team_event_verification import (
    HARNESS_ID,
    VerificationContextError,
    VerificationRequest,
    SupabaseVerificationSource,
    verify_nba_team_event,
)

ARTIFACT_TABLE = "wow_basketball_team_event_model_artifacts"
CALIBRATOR_TABLE = "wow_basketball_team_event_calibrators"
CORPUS_TABLE = "wow_nba_training_games"
RECEIPT_TABLE = "wow_independent_verification_receipts"

# Typed prerequisite failures. A missing migration must announce itself rather
# than surface later as a generic archive error.
LEDGER_MISSING_CODE = "NBA_VERIFICATION_LEDGER_MISSING"
CALIBRATOR_EVIDENCE_MISSING_CODE = "NBA_VERIFICATION_CALIBRATOR_EVIDENCE_MISSING"
PROBE_FAILED_CODE = "NBA_VERIFICATION_PREREQUISITE_PROBE_FAILED"

_MISSING_TABLE_CODES = frozenset({"42P01", "PGRST205"})
_MISSING_COLUMN_CODES = frozenset({"42703", "PGRST204"})
_LEDGER_PROBE_COLUMNS = (
    "receipt_id,receipt_digest,independent_verification_status,can_execute"
)
_CALIBRATOR_PROBE_COLUMNS = "calibration_sha256,calibration_fit_end"

CAN_EXECUTE = False
PUBLISHABLE_SCOPE = "NONE"

VERIFICATION_PASS = "PASS"
VERIFICATION_FAIL = "FAIL"
VERIFICATION_NOT_APPLICABLE = "NOT_APPLICABLE"

# V10's pending-capability blocker and V11's missing-attestation blockers are the
# only two families a maintenance run may defer. They are named here so a reader
# cannot mistake the deferral for a broad "ignore failures" switch.
PENDING_CERTIFICATION_BLOCKER = "CHECK_FAILED:V10"
MISSING_ATTESTATION_BLOCKER = "CHECK_FAILED:V11"
DEFERRED_GOVERNANCE_BLOCKERS = frozenset({
    PENDING_CERTIFICATION_BLOCKER,
    MISSING_ATTESTATION_BLOCKER,
})

PENDING_CAPABILITY_CODE = "CAPABILITY_STATUS_NOT_CERTIFIED"
PENDING_CAPABILITY_STATUS = "SHADOW"
_MISSING_ATTESTATION_PREFIXES = (
    "SOURCE_EVENT_REPLAY_NOT_ATTESTED:",
    "SOURCE_TIMESTAMP_NOT_ATTESTED:",
    "SOURCE_DATASET_ID_MISSING",
)

# These name the *components* on either side of the verification boundary, not
# human reviewers. A human independent-QA receipt is still required before any
# certification decision.
IMPLEMENTATION_OWNER = "wow-basketball-model-maintenance"
VERIFICATION_OWNER = "wow-v17-independent-verification-harness"


class VerificationArchiveError(RuntimeError):
    """Raised when a constructed receipt cannot be archived."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        super().__init__(detail or code)


@dataclass(frozen=True)
class VerificationIdentity:
    """Declared identities for the verification boundary."""

    controlling_specialist: str = "wow.nba-game-win-probability-expert"
    input_contract_version: str = "NBA_GAME_WIN_INPUTS_V1"
    independent_verifier: str = "INDEPENDENT_VERIFICATION"
    implementation_owner: str = IMPLEMENTATION_OWNER
    verification_owner: str = VERIFICATION_OWNER


def receipt_digest(receipt: Mapping[str, Any]) -> str:
    """Canonical digest of an archived receipt, recomputable by a reviewer."""
    canonical = json.dumps(
        dict(receipt), sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def default_evidence_refs(
    *, verification_id: str, artifact_id: str, model_artifact_version: str
) -> dict[str, str]:
    """Addresses of the exact rows the harness read, not assertions about them.

    Each reference resolves to a persisted row, so a reviewer can re-read it.
    The receipt's own ledger address is its verification id.
    """
    return {
        "corpus_evidence_ref": f"{CORPUS_TABLE}:sport=NBA,settled=true",
        "artifact_evidence_ref": f"{ARTIFACT_TABLE}:{artifact_id}",
        "calibration_evidence_ref": (
            f"{CALIBRATOR_TABLE}:model_artifact_version={model_artifact_version}"
        ),
        "replay_evidence_ref": f"{ARTIFACT_TABLE}:{artifact_id}#feature_replay",
        "independent_verification_evidence_ref": f"{RECEIPT_TABLE}:{verification_id}",
    }


def _check(receipt: Mapping[str, Any], check_id: str) -> Mapping[str, Any]:
    for entry in receipt.get("checks") or []:
        if isinstance(entry, Mapping) and entry.get("check_id") == check_id:
            return entry
    return {}


def _v10_is_only_pending_capability(receipt: Mapping[str, Any]) -> bool:
    """True when V10 failed *only* because the fresh artifact is not yet certified.

    Any additional V10 blocker (version drift, schema drift, certification
    digest mismatch) means the evidence itself disagrees, which is never
    deferrable.
    """
    entry = _check(receipt, "V10")
    if entry.get("status") != "FAIL":
        return False
    blockers = [str(blocker) for blocker in entry.get("blockers") or []]
    if not blockers:
        return False
    if not all(blocker.startswith(f"{PENDING_CAPABILITY_CODE}:") for blocker in blockers):
        return False
    observed = entry.get("observed") or {}
    return str(observed.get("capability_status") or "") == PENDING_CAPABILITY_STATUS


def _v11_is_only_missing_attestation(receipt: Mapping[str, Any]) -> bool:
    """True when V11 failed only because no independent attestation was supplied."""
    entry = _check(receipt, "V11")
    if entry.get("status") != "FAIL":
        return False
    blockers = [str(blocker) for blocker in entry.get("blockers") or []]
    if not blockers:
        return False
    return all(
        any(blocker.startswith(prefix) for prefix in _MISSING_ATTESTATION_PREFIXES)
        for blocker in blockers
    )


_DEFERRAL_PREDICATES: dict[str, Callable[[Mapping[str, Any]], bool]] = {
    PENDING_CERTIFICATION_BLOCKER: _v10_is_only_pending_capability,
    MISSING_ATTESTATION_BLOCKER: _v11_is_only_missing_attestation,
}


def classify_verdict(
    receipt: Mapping[str, Any], *, defer: Sequence[str] = ()
) -> dict[str, Any]:
    """Split a receipt's blockers into hard failures and explicit deferrals.

    `defer` defaults to empty: without an explicit, enumerated deferral a FAIL
    receipt always closes the gate.
    """
    status = str(receipt.get("independent_verification_status") or "")
    verdict = receipt.get("verdict") or {}
    deferred_requested = frozenset(str(code) for code in defer)
    hard: list[str] = []
    deferred: list[str] = []
    for blocker in (str(value) for value in verdict.get("blockers") or []):
        predicate = _DEFERRAL_PREDICATES.get(blocker)
        if blocker in deferred_requested and predicate is not None and predicate(receipt):
            deferred.append(blocker)
        else:
            hard.append(blocker)
    if status == VERIFICATION_PASS:
        gate_open = not hard and not deferred
    elif status == VERIFICATION_FAIL:
        # A deferred blocker must still leave the gate shut unless it was
        # actually deferred; a FAIL with no deferred blocker fails the run.
        gate_open = bool(deferred) and not hard
    else:
        gate_open = False
        hard.append(f"VERIFICATION_STATUS_UNKNOWN:{status or 'MISSING'}")
    return {
        "verification_status": status or None,
        "gate_open": gate_open,
        "hard_blockers": hard,
        "deferred_blockers": deferred,
    }


def resolve_artifact_id(client: Any, model_artifact_version: str) -> str | None:
    """Resolve the artifact row a fitting run just wrote, by version."""
    rows = (
        client.table(ARTIFACT_TABLE)
        .select("artifact_id")
        .eq("model_artifact_version", model_artifact_version)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
        .data
        or []
    )
    if not rows:
        return None
    artifact_id = str((rows[0] or {}).get("artifact_id") or "").strip()
    return artifact_id or None


def _probe_failure(exc: Exception) -> str:
    """Classify a probe error as a missing table, a missing column, or unknown."""
    # A PostgREST client raises with `.code`; a psycopg client raises with
    # `.sqlstate`. Both carry the same SQLSTATE the classification needs.
    provider_code = str(
        getattr(exc, "code", "") or getattr(exc, "sqlstate", "") or ""
    ).upper()
    message = str(exc).lower()
    if provider_code in _MISSING_TABLE_CODES or "could not find the table" in message:
        return "TABLE_MISSING"
    if "relation" in message and "does not exist" in message:
        return "TABLE_MISSING"
    if provider_code in _MISSING_COLUMN_CODES or (
        "column" in message and "does not exist" in message
    ):
        return "COLUMN_MISSING"
    if "could not find the" in message and "column" in message:
        return "COLUMN_MISSING"
    return "UNKNOWN"


def _probe(client: Any, table: str, columns: str) -> dict[str, Any]:
    """Read one row with the exact columns this wiring depends on."""
    try:
        client.table(table).select(columns).limit(1).execute()
    except Exception as exc:  # noqa: BLE001 - classified, never re-raised raw
        return {
            "table": table,
            "classification": _probe_failure(exc),
            "error_type": type(exc).__name__,
            "provider_code": str(
                getattr(exc, "code", "") or getattr(exc, "sqlstate", "") or ""
            )[:32]
            or None,
            "can_execute": False,
        }
    return {"table": table, "classification": "READY", "can_execute": False}


def verification_prerequisites(client: Any) -> dict[str, Any]:
    """Probe the storage independent verification needs, before any NBA fit.

    Verification always produces a receipt that must be archived, and the
    calibrator row must carry the digest and cutoff that V7/V10 compare against.
    When either is absent the run holds with one typed blocker instead of
    fitting an artifact it could never verify.

    Returns `ready`, a typed `code` (None when ready), `missing` entry names and
    per-table `probes`. It never raises and never asserts readiness it did not
    observe.
    """
    probes = [
        _probe(client, RECEIPT_TABLE, _LEDGER_PROBE_COLUMNS),
        _probe(client, CALIBRATOR_TABLE, _CALIBRATOR_PROBE_COLUMNS),
    ]
    missing: list[str] = []
    failures: list[dict[str, Any]] = []
    for probe in probes:
        classification = probe["classification"]
        if classification == "READY":
            continue
        if classification == "TABLE_MISSING":
            missing.append(str(probe["table"]))
        elif classification == "COLUMN_MISSING":
            missing.append(f"{probe['table']}.columns")
        else:
            failures.append({"stage": "VERIFICATION_PREREQUISITE_PROBE", **probe})

    code: str | None = None
    if RECEIPT_TABLE in missing:
        code = LEDGER_MISSING_CODE
    elif missing:
        code = CALIBRATOR_EVIDENCE_MISSING_CODE
    elif failures:
        code = PROBE_FAILED_CODE
    return {
        "ready": not missing and not failures,
        "code": code,
        "missing": missing,
        "probes": probes,
        "probe_failures": failures,
        "can_execute": False,
    }


def _is_duplicate(exc: Exception) -> bool:
    return getattr(exc, "code", None) == "23505" or "duplicate key" in str(exc).lower()


def archive_receipt(
    client: Any, receipt: Mapping[str, Any], *, digest: str | None = None
) -> dict[str, Any]:
    """Append the receipt to the immutable ledger. Never mutates an existing row."""
    resolved_digest = digest or receipt_digest(receipt)
    method = receipt.get("method") or {}
    verdict = receipt.get("verdict") or {}
    row = {
        "receipt_id": str(receipt.get("verification_id") or ""),
        "receipt_digest": resolved_digest,
        "schema_version": str(receipt.get("schema_version") or ""),
        "harness": str(method.get("harness") or HARNESS_ID),
        "code_sha": str(method.get("code_sha") or "UNRESOLVED"),
        "environment": method.get("environment"),
        "sport": str(receipt.get("sport") or ""),
        "league_scope": str(receipt.get("league_scope") or ""),
        "model_family": str(receipt.get("model_family") or ""),
        "model_version": str(receipt.get("model_version") or ""),
        "model_artifact_id": str(receipt.get("model_artifact_id") or ""),
        "model_artifact_sha256": str(receipt.get("model_artifact_sha256") or ""),
        "calibration_artifact_id": str(receipt.get("calibration_artifact_id") or ""),
        "calibration_sha256": str(receipt.get("calibration_sha256") or ""),
        "feature_schema_version": str(receipt.get("feature_schema_version") or ""),
        "input_contract_version": str(receipt.get("input_contract_version") or ""),
        "independent_verifier": str(receipt.get("independent_verifier") or ""),
        "implementation_owner": str(receipt.get("implementation_owner") or ""),
        "verification_owner": str(receipt.get("verification_owner") or ""),
        "independent_verification_status": str(
            receipt.get("independent_verification_status") or ""
        ),
        "publishable_scope": str(verdict.get("publishable_scope") or PUBLISHABLE_SCOPE),
        "certification_eligible": verdict.get("certification_eligible") is True,
        "failed_checks": [
            str(entry.get("check_id"))
            for entry in receipt.get("checks") or []
            if isinstance(entry, Mapping) and entry.get("status") == "FAIL"
        ],
        "blockers": [str(value) for value in verdict.get("blockers") or []],
        "receipt": dict(receipt),
        "can_execute": False,
    }
    try:
        client.table(RECEIPT_TABLE).insert(row).execute()
    except Exception as exc:  # noqa: BLE001 - archive failure must be typed and visible
        if _is_duplicate(exc):
            return {"archived": True, "duplicate": True, "receipt_digest": resolved_digest}
        raise VerificationArchiveError(
            "NBA_VERIFICATION_ARCHIVE_UNAVAILABLE",
            f"{type(exc).__name__}:{exc}",
        ) from exc
    return {"archived": True, "duplicate": False, "receipt_digest": resolved_digest}


def verify_and_archive(
    client: Any,
    *,
    model_artifact_version: str,
    identity: VerificationIdentity | None = None,
    attestation: Mapping[str, Any] | None = None,
    artifact_id: str | None = None,
    evidence_refs: Mapping[str, str] | None = None,
    source: Any | None = None,
    verification_id: str | None = None,
    code_sha: str | None = None,
    environment: str = "GOVERNED_BASKETBALL_MAINTENANCE",
    max_ece: float | None = None,
    as_of: Any | None = None,
    defer: Sequence[str] = (),
) -> dict[str, Any]:
    """Verify one fitted NBA artifact from persisted rows, then archive the receipt.

    Raises `VerificationContextError` when a receipt cannot be constructed (the
    absence of evidence is not a digest) and `VerificationArchiveError` when the
    ledger write fails. Neither is converted into a soft success here.
    """
    version = str(model_artifact_version or "").strip()
    if not version:
        raise VerificationContextError("MODEL_ARTIFACT_VERSION_MISSING")
    resolved_id = artifact_id or resolve_artifact_id(client, version)
    if not resolved_id:
        raise VerificationContextError("ARTIFACT_ROW_NOT_FOUND", version)

    request_identity = identity or VerificationIdentity()
    resolved_verification_id = verification_id or str(uuid.uuid4())
    request = VerificationRequest(
        artifact_id=resolved_id,
        controlling_specialist=request_identity.controlling_specialist,
        input_contract_version=request_identity.input_contract_version,
        independent_verifier=request_identity.independent_verifier,
        implementation_owner=request_identity.implementation_owner,
        verification_owner=request_identity.verification_owner,
        evidence_refs=dict(
            evidence_refs
            or default_evidence_refs(
                verification_id=resolved_verification_id,
                artifact_id=resolved_id,
                model_artifact_version=version,
            )
        ),
        max_ece=max_ece,
        as_of=as_of,
    )
    active_source = source or SupabaseVerificationSource(
        client, attestation=dict(attestation or {})
    )
    receipt = verify_nba_team_event(
        active_source,
        request,
        code_sha=code_sha,
        environment=environment,
        verification_id=resolved_verification_id,
    )
    classification = classify_verdict(receipt, defer=defer)
    digest = receipt_digest(receipt)
    archived = archive_receipt(client, receipt, digest=digest)
    return {
        "receipt": receipt,
        "receipt_digest": digest,
        "archived": archived,
        **classification,
    }


__all__ = [
    "ARTIFACT_TABLE",
    "CAN_EXECUTE",
    "DEFERRED_GOVERNANCE_BLOCKERS",
    "CALIBRATOR_EVIDENCE_MISSING_CODE",
    "IMPLEMENTATION_OWNER",
    "LEDGER_MISSING_CODE",
    "MISSING_ATTESTATION_BLOCKER",
    "PROBE_FAILED_CODE",
    "PENDING_CERTIFICATION_BLOCKER",
    "RECEIPT_TABLE",
    "VERIFICATION_FAIL",
    "VERIFICATION_NOT_APPLICABLE",
    "VERIFICATION_PASS",
    "VERIFICATION_OWNER",
    "VerificationArchiveError",
    "VerificationIdentity",
    "archive_receipt",
    "classify_verdict",
    "default_evidence_refs",
    "receipt_digest",
    "resolve_artifact_id",
    "verification_prerequisites",
    "verify_and_archive",
]
