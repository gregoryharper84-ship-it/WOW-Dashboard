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
    db: Any, *, scored: Mapping[str, Any], home_team: str,
    away_team: str, event_start_time_utc: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Append one pregame research receipt, with deterministic collision proof.

    A valid scored receipt is necessary but not sufficient for trust: the
    invoking server must already have independently read back the canonical
    frozen source and approved certification. No caller-supplied probabilities
    are accepted as fitted outputs by this module alone.
    """
    if not isinstance(scored, Mapping) or any((
        scored.get("status") != "SCORED",
        scored.get("code") != "NBA_PREGAME_SCORED",
        scored.get("sport") != "NBA",
        scored.get("probability_publishable") is not False,
        scored.get("rank_eligible") is not False,
        scored.get("can_execute") is not False,
    )):
        raise NBAShadowReceiptHold("NBA_SHADOW_CERTIFIED_SCORE_REQUIRED")
    timestamp = _utc(now or datetime.now(timezone.utc))
    event_at = _utc(event_start_time_utc)
    source_at = _utc(scored.get("source_snapshot_timestamp"))
    feature_at = _utc(scored.get("feature_timestamp"))
    if not source_at <= feature_at <= timestamp < event_at:
        raise NBAShadowReceiptHold("NBA_SHADOW_NOT_PROSPECTIVE")
    event_id = _required(scored.get("official_event_id"), "NBA_SHADOW_EVENT_ID_MISSING")
    if not event_id.startswith("espn-"):
        raise NBAShadowReceiptHold("NBA_SHADOW_EVENT_ID_INVALID")
    home = _required(home_team, "NBA_SHADOW_TEAM_MISSING")
    away = _required(away_team, "NBA_SHADOW_TEAM_MISSING")
    if home == away:
        raise NBAShadowReceiptHold("NBA_SHADOW_TEAMS_IDENTICAL")
    source_id = _required(scored.get("source_snapshot_id"), "NBA_SHADOW_SOURCE_ID_MISSING")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", source_id):
        raise NBAShadowReceiptHold("NBA_SHADOW_SOURCE_ID_INVALID")
    identity = {
        "official_event_id": event_id, "source_snapshot_id": source_id,
        "model_artifact_id": _required(scored.get("model_artifact_id"), "NBA_SHADOW_MODEL_ID_MISSING"),
        "calibration_artifact_id": _required(scored.get("calibration_artifact_id"), "NBA_SHADOW_CALIBRATOR_ID_MISSING"),
    }
    digests = {
        "model_artifact_sha256": scored.get("model_artifact_sha256"),
        "calibration_artifact_sha256": scored.get("calibration_artifact_sha256"),
    }
    if not all(isinstance(v, str) and re.fullmatch(r"[0-9a-f]{64}", v)
               for v in digests.values()):
        raise NBAShadowReceiptHold("NBA_SHADOW_DIGEST_INVALID")
    raw = _p(scored.get("raw_probability"))
    point = _p(scored.get("calibrated_probability"))
    lo = _p(scored.get("calibrated_lower_bound"))
    hi = _p(scored.get("calibrated_upper_bound"))
    if not lo <= point <= hi:
        raise NBAShadowReceiptHold("NBA_SHADOW_BOUNDS_INVALID")
    exact = {
        **identity, **digests,
        "event_start_time_utc": event_at.isoformat(),
        "home_team": home, "away_team": away,
        "source_snapshot_timestamp": source_at.isoformat(),
        "certification_id": _required(scored.get("certification_id"), "NBA_SHADOW_CERT_ID_MISSING"),
        "model_artifact_version": _required(scored.get("model_artifact_version"), "NBA_SHADOW_MODEL_VERSION_MISSING"),
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
        or not persisted.get("nba_prediction_id")
        or persisted.get("probability_publishable") is not False
        or persisted.get("rank_eligible") is not False
        or persisted.get("can_execute") is not False
        or persisted.get("terminal_label") != "MODEL_RESEARCH_ONLY_HOLD"
    ):
        raise NBAShadowReceiptHold("NBA_SHADOW_IMMUTABLE_READBACK_CONFLICT")
    return {"status": status, "nba_prediction_id": persisted["nba_prediction_id"],
            "probability_publishable": False, "rank_eligible": False, "can_execute": False}
