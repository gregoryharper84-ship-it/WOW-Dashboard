"""Server-clock timestamp attestation for pregame source captures (Incident #1950).

The NBA/NCAAB scorers require ``timestamp_attestation == "VERIFIED"`` before
any canonical pregame feature package is accepted. Runner clocks and HTTP Date
headers are not independent, so until now no producer could ever meet that
bar. This module supplies the missing independent clock:

* the raw capture hash, its ``sha256:`` snapshot id and the derived feature
  payload hash are inserted into ``wow_v17_source_capture_attestations``;
* the database assigns ``received_at`` with ``clock_timestamp()`` in a BEFORE
  INSERT trigger (callers never send it), and its check constraint rejects any
  row stamped at or after the event start;
* update/delete are revoked, so a receipt cannot be rewritten later;
* the row is read back and every bound field is compared before VERIFIED is
  returned.

Attestation is input-timing evidence only. It never certifies a model, sets a
probability, or authorizes publication. Any failure is typed and leaves the
package at ``timestamp_attestation = "NOT_ESTABLISHED"`` (fail closed).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import re
from typing import Any, Mapping

CAN_EXECUTE = False
ATTESTATION_TABLE = "wow_v17_source_capture_attestations"
ATTESTATION_POLICY = "SUPABASE_SERVER_CLOCK_RECEIPT_V1"
REQUIRED_REPLAY_STATUS = "RAW_CAPTURE_HASH_AND_EVENT_REPLAY_PASS"
# The server clock may lead the caller's clock slightly; it may not be far ahead.
MAX_SERVER_CLOCK_LEAD = timedelta(minutes=5)
_HASH = re.compile(r"^[0-9a-f]{64}$")
_SPORT = re.compile(r"^[A-Z]{2,8}$")


class SourceCaptureAttestationError(RuntimeError):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code = code


def _aware(value: Any, code: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        except ValueError as exc:
            raise SourceCaptureAttestationError(code) from exc
    if parsed.tzinfo is None:
        raise SourceCaptureAttestationError(code)
    return parsed.astimezone(timezone.utc)


def feature_payload_sha256(features: Mapping[str, Any]) -> str:
    """Canonical digest of the exact feature mapping that will be scored."""
    if not isinstance(features, Mapping) or not features:
        raise SourceCaptureAttestationError("SOURCE_CAPTURE_FEATURES_MISSING")
    try:
        canonical = json.dumps(dict(features), sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise SourceCaptureAttestationError("SOURCE_CAPTURE_PAYLOAD_INVALID") from exc
    return sha256(canonical.encode("utf-8")).hexdigest()


def _is_duplicate(exc: BaseException) -> bool:
    code = str(getattr(exc, "code", "") or "")
    return code == "23505" or "duplicate key value" in str(exc)


def attest_source_capture(
    client: Any,
    *,
    sport: str,
    official_event_id: str,
    event_start_time_utc: Any,
    source_raw_sha256: str,
    feature_sha256: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Commit the capture hashes and return a VERIFIED server-clock receipt.

    Raises ``SourceCaptureAttestationError`` on any input, write, read-back or
    chronology failure. Never sends ``received_at``.
    """
    sport = str(sport or "").strip().upper()
    event_id = str(official_event_id or "").strip()
    raw = str(source_raw_sha256 or "").strip().lower()
    feature = str(feature_sha256 or "").strip().lower()
    if not _SPORT.fullmatch(sport) or not event_id or len(event_id) > 128:
        raise SourceCaptureAttestationError("SOURCE_CAPTURE_IDENTITY_INVALID")
    if not _HASH.fullmatch(raw) or not _HASH.fullmatch(feature):
        raise SourceCaptureAttestationError("SOURCE_CAPTURE_HASH_INVALID")
    event_at = _aware(event_start_time_utc, "SOURCE_CAPTURE_EVENT_START_INVALID")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if event_at <= current:
        raise SourceCaptureAttestationError("SOURCE_CAPTURE_EVENT_NOT_PREGAME")
    snapshot_id = "sha256:" + raw
    row = {
        "sport": sport,
        "official_event_id": event_id,
        "event_start_time_utc": event_at.isoformat(),
        "source_snapshot_id": snapshot_id,
        "source_raw_sha256": raw,
        "feature_payload_sha256": feature,
        "attestation_policy": ATTESTATION_POLICY,
        "can_execute": False,
    }
    try:
        client.table(ATTESTATION_TABLE).insert(row).execute()
    except Exception as exc:  # noqa: BLE001 - typed below
        # An identical earlier receipt is an idempotent replay; it is still
        # verified field by field from the read-back below.
        if not _is_duplicate(exc):
            raise SourceCaptureAttestationError("SOURCE_CAPTURE_ATTESTATION_WRITE_FAILED") from exc
    try:
        rows = (
            client.table(ATTESTATION_TABLE)
            .select("attestation_id,received_at,sport,official_event_id,event_start_time_utc,"
                    "source_snapshot_id,source_raw_sha256,feature_payload_sha256,"
                    "attestation_policy,can_execute")
            .eq("sport", sport).eq("official_event_id", event_id)
            .eq("source_snapshot_id", snapshot_id).eq("feature_payload_sha256", feature)
            .execute().data
        )
    except Exception as exc:  # noqa: BLE001 - typed
        raise SourceCaptureAttestationError("SOURCE_CAPTURE_ATTESTATION_READ_FAILED") from exc
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], Mapping):
        raise SourceCaptureAttestationError("SOURCE_CAPTURE_ATTESTATION_NOT_FOUND")
    stored = rows[0]
    for name in ("sport", "official_event_id", "source_snapshot_id",
                 "source_raw_sha256", "feature_payload_sha256"):
        if str(stored.get(name) or "") != str(row[name]):
            raise SourceCaptureAttestationError("SOURCE_CAPTURE_ATTESTATION_MISMATCH")
    if stored.get("attestation_policy") != ATTESTATION_POLICY or stored.get("can_execute") is not False:
        raise SourceCaptureAttestationError("SOURCE_CAPTURE_ATTESTATION_MISMATCH")
    if _aware(stored.get("event_start_time_utc"), "SOURCE_CAPTURE_ATTESTATION_MISMATCH") != event_at:
        raise SourceCaptureAttestationError("SOURCE_CAPTURE_ATTESTATION_MISMATCH")
    received_at = _aware(stored.get("received_at"), "SOURCE_CAPTURE_SERVER_TIME_MISSING")
    if received_at >= event_at:
        raise SourceCaptureAttestationError("SOURCE_CAPTURE_ATTESTED_AFTER_EVENT_START")
    if received_at > current + MAX_SERVER_CLOCK_LEAD:
        raise SourceCaptureAttestationError("SOURCE_CAPTURE_SERVER_CLOCK_IMPLAUSIBLE")
    attestation_id = str(stored.get("attestation_id") or "").strip()
    if not attestation_id:
        raise SourceCaptureAttestationError("SOURCE_CAPTURE_ATTESTATION_NOT_FOUND")
    return {
        "timestamp_attestation": "VERIFIED",
        "timestamp_attestation_policy": ATTESTATION_POLICY,
        "attestation_id": attestation_id,
        "attested_at": received_at.isoformat(),
        "source_snapshot_id": snapshot_id,
        "feature_payload_sha256": feature,
        "can_execute": False,
    }


def attest_bound_source_features(
    client: Any,
    bound_package: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Upgrade a raw-capture-bound feature package to a VERIFIED timestamp.

    Input is the output of a raw-capture binder such as
    ``bind_nba_frozen_source_to_features`` (``source_replay_status`` must
    already be ``RAW_CAPTURE_HASH_AND_EVENT_REPLAY_PASS``). On success the
    audit's ``as_of`` becomes the server ``received_at``, the conservative
    instant by which the features provably existed. On any failure the package stays NOT_ESTABLISHED, stale attestation
    identifiers are cleared, and all publication and execution flags are false.
    """
    result = dict(bound_package)
    raw_audit = bound_package.get("feature_audit")
    audit = dict(raw_audit) if isinstance(raw_audit, Mapping) else {}
    result["feature_audit"] = audit
    result.update(probability_publishable=False, rank_eligible=False, can_execute=False)
    try:
        if raw_audit is not None and not isinstance(raw_audit, Mapping):
            raise SourceCaptureAttestationError("SOURCE_CAPTURE_PAYLOAD_INVALID")
        if audit.get("source_replay_status") != REQUIRED_REPLAY_STATUS:
            raise SourceCaptureAttestationError("SOURCE_CAPTURE_REPLAY_NOT_VERIFIED")
        raw = str(audit.get("source_raw_sha256") or "").lower()
        if bound_package.get("source_snapshot_id") != "sha256:" + raw:
            raise SourceCaptureAttestationError("SOURCE_CAPTURE_SNAPSHOT_ID_MISMATCH")
        receipt = attest_source_capture(
            client,
            sport=str(bound_package.get("sport") or ""),
            official_event_id=str(bound_package.get("official_event_id") or ""),
            event_start_time_utc=bound_package.get("event_start_time_utc"),
            source_raw_sha256=raw,
            feature_sha256=feature_payload_sha256(bound_package.get("features") or {}),
            now=now,
        )
    except SourceCaptureAttestationError as exc:
        audit["timestamp_attestation"] = "NOT_ESTABLISHED"
        audit["timestamp_attestation_failure"] = exc.code
        audit.pop("timestamp_attestation_id", None)
        audit.pop("timestamp_attestation_policy", None)
        return result
    audit.update({
        "timestamp_attestation": "VERIFIED",
        "timestamp_attestation_policy": receipt["timestamp_attestation_policy"],
        "timestamp_attestation_id": receipt["attestation_id"],
        "as_of": receipt["attested_at"],
    })
    audit.pop("timestamp_attestation_failure", None)
    return result


__all__ = [
    "ATTESTATION_POLICY",
    "ATTESTATION_TABLE",
    "SourceCaptureAttestationError",
    "attest_bound_source_features",
    "attest_source_capture",
    "feature_payload_sha256",
]
