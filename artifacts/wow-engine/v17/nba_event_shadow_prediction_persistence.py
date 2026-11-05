"""NBA team-event forward shadow receipt: write once, read back, never publish.

The caller must be the authenticated server AFTER source/timestamp attestation,
fitted-specialist certification and independently approved dynamic bounds.
This function does not authenticate an arbitrary caller or grant certification.
It is deliberately not registered as a public route or automatic scorer hook.
"""

from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import re
from typing import Any, Mapping

TABLE = "wow_nba_event_shadow_predictions"
CAN_EXECUTE = False
IDENTITY = ("official_event_id", "source_snapshot_id",
            "model_artifact_id", "calibration_artifact_id")
# Bound columns compared directly against the readback row. A digest column is
# not proof: these are the immutable identity/probability values a genuine
# receipt must carry, independent of any hash the writer supplied.
TIMESTAMP_BOUND_FIELDS = ("event_start_time_utc", "source_snapshot_timestamp")
NUMERIC_BOUND_FIELDS = (
    "raw_home_probability", "calibrated_home_probability",
    "calibrated_home_lower_bound", "calibrated_home_upper_bound",
)
_UUID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)


class NBAShadowReceiptHold(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _utc(value: Any) -> datetime:
    try:
        d = value if isinstance(value, datetime) else datetime.fromisoformat(
            str(value).replace("Z", "+00:00"))
        if d.utcoffset() is not None:
            return d.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        pass
    raise NBAShadowReceiptHold("NBA_SHADOW_TIMESTAMP_INVALID")


def _p(value: Any) -> float:
    try:
        if isinstance(value, bool):
            raise ValueError("boolean")
        n = float(value)
        if math.isfinite(n) and 0 < n < 1:
            return n
    except (ValueError, TypeError, OverflowError):
        pass
    raise NBAShadowReceiptHold("NBA_SHADOW_PROBABILITY_INVALID")


def _required(value: Any, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise NBAShadowReceiptHold(code)
    return value.strip()


def _instant(value: Any) -> datetime | None:
    try:
        return _utc(value)
    except NBAShadowReceiptHold:
        return None


def _verify_persisted_receipt(
    persisted: Mapping[str, Any],
    *,
    exact: Mapping[str, Any],
    model_timestamp: datetime,
    source_at: datetime,
    event_at: datetime,
) -> None:
    """Prove the stored row is this server-recorded pregame receipt.

    A matching digest column and a non-empty primary key do not independently
    prove a genuine immutable receipt. The database-generated identity, the
    database insertion clock, and every bound identity/timestamp/probability
    column are re-read and checked here, so a stored row that does not carry
    the submitted evidence fails closed even if a caller supplied the digest.
    """
    prediction_id = persisted.get("nba_prediction_id")
    if not isinstance(prediction_id, str) or not _UUID.fullmatch(
        prediction_id.strip().lower()
    ):
        raise NBAShadowReceiptHold("NBA_SHADOW_READBACK_INVALID")
    model_ts = _instant(persisted.get("model_timestamp"))
    created_at = _instant(persisted.get("created_at"))
    if model_ts is None or created_at is None:
        raise NBAShadowReceiptHold("NBA_SHADOW_READBACK_INVALID")
    # The scoring instant and the durable insertion must both precede tip-off.
    # Without this, "HOLD" could describe a row recorded after the result.
    if not (source_at <= model_ts < event_at) or model_ts != model_timestamp:
        raise NBAShadowReceiptHold("NBA_SHADOW_NOT_PROSPECTIVE")
    if created_at >= event_at:
        raise NBAShadowReceiptHold("NBA_SHADOW_NOT_PROSPECTIVE")
    for field, expected in exact.items():
        stored = persisted.get(field)
        if field in TIMESTAMP_BOUND_FIELDS:
            matches = _instant(stored) == _instant(expected)
        elif field in NUMERIC_BOUND_FIELDS:
            try:
                matches = _p(stored) == _p(expected)
            except NBAShadowReceiptHold:
                matches = False
        else:
            matches = isinstance(stored, str) and stored == expected
        if not matches:
            raise NBAShadowReceiptHold("NBA_SHADOW_IMMUTABLE_READBACK_CONFLICT")


def _read(db: Any, identity: Mapping[str, str]) -> Mapping[str, Any] | None:
    try:
        q = db.table(TABLE).select("*")
        for field in IDENTITY:
            q = q.eq(field, identity[field])
        rows = q.limit(2).execute().data
    except Exception as exc:
        raise NBAShadowReceiptHold("NBA_SHADOW_READBACK_FAILED") from exc
    if not isinstance(rows, list) or len(rows) > 1 or (
        rows and not isinstance(rows[0], Mapping)
    ):
        raise NBAShadowReceiptHold("NBA_SHADOW_READBACK_INVALID")
    return rows[0] if rows else None


def persist_nba_shadow_prediction(
    db: Any, *, scored: Mapping[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Append one forward-only research receipt from the *whole* team scorer.

    A nested fitted-scorer receipt is not independently a team-event result.
    The registered NBA bridge owns the outer result, exact event identity,
    team IDs, time and V17 terminal HOLD. An internal authenticated collector
    must additionally prove server-owned source attestation before calling this
    library; this is not a callable public route or authorization service.
    """
    if not isinstance(scored, Mapping) or any((
        scored.get("sport") != "NBA",
        scored.get("model_status") != "RESEARCH_ONLY_NOT_FOR_PUBLICATION",
        scored.get("global_terminal_authority") != "V17_TERMINAL_REDUCER",
        scored.get("terminal_label") != "MODEL_RESEARCH_ONLY_HOLD",
        scored.get("model_invoked") is not True,
        scored.get("probability_publishable") is not False,
        scored.get("rank_eligible") is not False,
        scored.get("can_execute") is not False,
    )):
        raise NBAShadowReceiptHold("NBA_SHADOW_GOVERNED_RESULT_REQUIRED")
    receipt = scored.get("nba_receipt")
    if not isinstance(receipt, Mapping) or any((
        receipt.get("status") != "SCORED",
        receipt.get("code") != "NBA_PREGAME_SCORED",
        receipt.get("sport") != "NBA",
        receipt.get("probability_publishable") is not False,
        receipt.get("rank_eligible") is not False,
        receipt.get("can_execute") is not False,
    )):
        raise NBAShadowReceiptHold("NBA_SHADOW_CERTIFIED_SCORE_REQUIRED")

    # Binding to both scorer stages prevents callers accidentally substituting
    # an unrelated event, source, version, calibration or probability. The
    # historical NBA training namespace is 'espn-' from SportsDataverse ESPN;
    # never infer a different provider's team crosswalk from display names.
    bound_fields = (
        ("official_event_id", "official_event_id"),
        ("source_snapshot_id", "source_snapshot_id"),
        ("source_snapshot_timestamp", "source_snapshot_timestamp"),
        ("model_artifact_version", "model_version"),
        ("calibration_artifact_sha256", "calibration_artifact_fingerprint"),
    )
    if any(
        str(receipt.get(inner) or "") != str(scored.get(outer) or "")
        or not receipt.get(inner)
        for inner, outer in bound_fields
    ):
        raise NBAShadowReceiptHold("NBA_SHADOW_SCORER_RECEIPT_MISMATCH")
    for inner, outer in (
        ("raw_probability", "raw_model_probability"),
        ("calibrated_probability", "calibrated_probability"),
        ("calibrated_lower_bound", "calibrated_lower_bound"),
        ("calibrated_upper_bound", "calibrated_upper_bound"),
    ):
        if _p(receipt.get(inner)) != _p(scored.get(outer)):
            raise NBAShadowReceiptHold("NBA_SHADOW_SCORER_RECEIPT_MISMATCH")

    timestamp = _utc(now or datetime.now(timezone.utc))
    event_at = _utc(scored.get("event_start_time_utc"))
    source_at = _utc(receipt.get("source_snapshot_timestamp"))
    feature_at = _utc(receipt.get("feature_timestamp"))
    if not source_at <= feature_at <= timestamp < event_at:
        raise NBAShadowReceiptHold("NBA_SHADOW_NOT_PROSPECTIVE")
    event_id = _required(receipt.get("official_event_id"), "NBA_SHADOW_EVENT_ID_MISSING")
    if not re.fullmatch(r"espn-[0-9]+", event_id):
        raise NBAShadowReceiptHold("NBA_SHADOW_EVENT_ID_INVALID")
    home = _required(receipt.get("home_team_id"), "NBA_SHADOW_TEAM_MISSING")
    away = _required(receipt.get("away_team_id"), "NBA_SHADOW_TEAM_MISSING")
    if (
        not re.fullmatch(r"espn-[0-9]+", home)
        or not re.fullmatch(r"espn-[0-9]+", away)
        or home == away
    ):
        raise NBAShadowReceiptHold("NBA_SHADOW_CANONICAL_TEAM_ID_INVALID")
    source_id = _required(receipt.get("source_snapshot_id"), "NBA_SHADOW_SOURCE_ID_MISSING")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", source_id):
        raise NBAShadowReceiptHold("NBA_SHADOW_SOURCE_ID_INVALID")
    identity = {
        "official_event_id": event_id, "source_snapshot_id": source_id,
        "model_artifact_id": _required(receipt.get("model_artifact_id"), "NBA_SHADOW_MODEL_ID_MISSING"),
        "calibration_artifact_id": _required(receipt.get("calibration_artifact_id"), "NBA_SHADOW_CALIBRATOR_ID_MISSING"),
    }
    digests = {
        "model_artifact_sha256": receipt.get("model_artifact_sha256"),
        "calibration_artifact_sha256": receipt.get("calibration_artifact_sha256"),
    }
    if not all(isinstance(v, str) and re.fullmatch(r"[0-9a-f]{64}", v)
               for v in digests.values()):
        raise NBAShadowReceiptHold("NBA_SHADOW_DIGEST_INVALID")
    raw = _p(receipt.get("raw_probability"))
    point = _p(receipt.get("calibrated_probability"))
    lo = _p(receipt.get("calibrated_lower_bound"))
    hi = _p(receipt.get("calibrated_upper_bound"))
    if not lo <= point <= hi:
        raise NBAShadowReceiptHold("NBA_SHADOW_BOUNDS_INVALID")
    exact = {
        **identity, **digests,
        "event_start_time_utc": event_at.isoformat(),
        "home_team_id": home, "away_team_id": away,
        "source_snapshot_timestamp": source_at.isoformat(),
        "certification_id": _required(receipt.get("certification_id"), "NBA_SHADOW_CERT_ID_MISSING"),
        "model_artifact_version": _required(receipt.get("model_artifact_version"), "NBA_SHADOW_MODEL_VERSION_MISSING"),
        "raw_home_probability": raw, "calibrated_home_probability": point,
        "calibrated_home_lower_bound": lo, "calibrated_home_upper_bound": hi,
    }
    digest = sha256(json.dumps(exact, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    candidate = {
        **exact, "prediction_sha256": digest, "model_timestamp": timestamp.isoformat(),
        "terminal_label": "MODEL_RESEARCH_ONLY_HOLD",
        "probability_publishable": False, "rank_eligible": False, "can_execute": False,
    }
    original = _read(db, identity)
    if original is None:
        try:
            db.table(TABLE).insert(candidate).execute()
        except Exception:
            # A concurrent identical insert or lost acknowledgement is resolved
            # by one exact readback, never by an unbounded retry or UPDATE.
            pass
        persisted = _read(db, identity)
        status = "PERSISTED_RESEARCH_ONLY"
    else:
        persisted = original
        status = "ALREADY_PERSISTED_RESEARCH_ONLY"
    if persisted is None:
        raise NBAShadowReceiptHold("NBA_SHADOW_WRITE_NOT_PERSISTED")
    if (
        persisted.get("prediction_sha256") != digest
        or persisted.get("probability_publishable") is not False
        or persisted.get("rank_eligible") is not False
        or persisted.get("can_execute") is not False
        or persisted.get("terminal_label") != "MODEL_RESEARCH_ONLY_HOLD"
    ):
        raise NBAShadowReceiptHold("NBA_SHADOW_IMMUTABLE_READBACK_CONFLICT")
    _verify_persisted_receipt(
        persisted, exact=exact, model_timestamp=timestamp,
        source_at=source_at, event_at=event_at,
    )
    return {"status": status, "nba_prediction_id": persisted["nba_prediction_id"],
            "probability_publishable": False, "rank_eligible": False, "can_execute": False}
